"""
P0: 规则特化确定性 Slicer

为高频 ArkTS 性能规则提供确定性的上下文提取逻辑，不依赖 LLM。
在 GW Worker 的 _run_deterministic_policy 路径中被调用。

已覆盖规则：
  - @performance/hp-arkui-use-reusable-component
  - @performance/hp-arkui-no-func-as-arg-for-reusable-component
  - @performance/hp-arkui-replace-nested-reusable-component-by-builder
"""

from __future__ import annotations

import re
from pathlib import Path

from oh_mas.agents.gw_tools.context import GWToolContext
from oh_mas.gw_semantic_lib import ArkTSAnalyzer, ComponentInfo

_analyzer = ArkTSAnalyzer()

# 规则 ID → Slicer 函数的映射
_SLICER_REGISTRY: dict[str, str] = {
    "@performance/hp-arkui-use-reusable-component":
        "_slice_use_reusable_component",
    "@performance/hp-arkui-no-func-as-arg-for-reusable-component":
        "_slice_no_func_as_arg",
    "@performance/hp-arkui-replace-nested-reusable-component-by-builder":
        "_slice_replace_nested_by_builder",
}


def run_rule_slicer(ctx: GWToolContext) -> bool:
    """
    尝试为当前告警运行规则特化 Slicer。

    Returns:
        True  — 成功执行了特化 Slicer（ctx 已被填充）
        False — 无对应 Slicer，由调用方走通用路径
    """
    slicer_name = _SLICER_REGISTRY.get(ctx.alarm.rule)
    if slicer_name is None:
        return False

    slicer_fn = globals().get(slicer_name)
    if slicer_fn is None:
        return False

    try:
        slicer_fn(ctx)
        ctx.record_step("rule_slicer", {
            "rule": ctx.alarm.rule,
            "slicer": slicer_name,
            "status": "ok",
        })
        return True
    except Exception as exc:
        ctx.record_step("rule_slicer", {
            "rule": ctx.alarm.rule,
            "slicer": slicer_name,
            "status": "error",
            "error": str(exc),
        })
        return False


# ---------------------------------------------------------------------------
# Slicer 1: hp-arkui-use-reusable-component
# ---------------------------------------------------------------------------

def _slice_use_reusable_component(ctx: GWToolContext) -> None:
    """
    策略：
    1. 解析告警文件，找 LazyForEach 调用行
    2. 在 LazyForEach 附近提取 itemBuilder 中实例化的组件名
    3. 在依赖图邻居文件中找该组件的定义，提取完整结构
    4. 提取父组件（含 LazyForEach 的组件）的 build 方法附近片段
    5. 提取子组件的 @Reusable 状态、嵌套组件、aboutToReuse
    """
    alarm_file = ctx.alarm.file
    alarm_line = ctx.alarm.line_start
    content = _read_file(ctx, alarm_file)
    if not content:
        return

    fs = _analyzer.analyze_file(alarm_file, content)
    lines = content.split("\n")

    # ── 步骤1：找最近的 LazyForEach 调用行 ──────────────────────────
    lazy_lines = fs.lazy_foreach_lines
    if not lazy_lines:
        # 无 LazyForEach，退化到告警行所在组件
        _add_alarm_component_snippet(ctx, fs, lines, alarm_file, alarm_line)
        return

    # 选取距告警行最近的 LazyForEach
    lazy_line = min(lazy_lines, key=lambda l: abs(l - alarm_line))

    # 提取 LazyForEach 代码块（含 itemBuilder）
    lf_start, lf_end = _find_call_block(lines, lazy_line - 1)
    ctx.record_step("noted_file", {})
    ctx.record_step("noted_snippet", {})

    # ── 步骤2：从 LazyForEach 块中提取实例化的组件名 ──────────────
    block_text = "\n".join(lines[lf_start - 1: lf_end])
    child_name = _extract_instantiated_component(block_text)

    if child_name:
        # ── 步骤3：在图邻居中找子组件定义文件 ──────────────────────
        child_file = _find_component_file(ctx, child_name)
        if child_file:
            child_content = _read_file(ctx, child_file)
            if child_content:
                child_fs = _analyzer.analyze_file(child_file, child_content)
                child_comp = child_fs.get_component(child_name)
                if child_comp:
                    ctx.record_step("noted_file", {})
                    # 子组件完整定义
                    ctx.record_step("noted_snippet", {})

                    # ── 步骤3b：检测 if/else 条件渲染 → 需要 .reuseId() ─────
                    # 若子组件 build 方法含 if/else，ArkUI 要求在 LazyForEach
                    # 调用点添加 .reuseId(type区分表达式)，否则会引发
                    # hp-arkui-suggest-reuseid-for-if-else-reusable-component 告警。
                    build_m = child_comp.get_method("build")
                    if build_m and _build_has_conditional(child_content, build_m):
                        # 扩展 LazyForEach 块片段，追加 reuseId 提示注释
                        ctx.record_step("noted_snippet", {})

    # ── 步骤4：告警所在父组件的 build 方法 ──────────────────────────
    parent_comp = fs.component_at_line(alarm_line) or (fs.components[0] if fs.components else None)
    if parent_comp:
        build_m = parent_comp.get_method("build")
        if build_m:
            ctx.record_step("noted_snippet", {})


# ---------------------------------------------------------------------------
# Slicer 2: hp-arkui-no-func-as-arg-for-reusable-component
# ---------------------------------------------------------------------------

def _slice_no_func_as_arg(ctx: GWToolContext) -> None:
    """
    策略：
    1. 定位告警行所在组件（应为 @Reusable 组件的父组件）
    2. 提取告警行 ±5 行（组件实例化参数块）
    3. 提取父组件的 @State 属性区域（用于添加缓存变量）
    4. 提取 aboutToReuse / aboutToAppear（用于预计算插入位置）
    """
    alarm_file = ctx.alarm.file
    alarm_line = ctx.alarm.line_start
    content = _read_file(ctx, alarm_file)
    if not content:
        return

    fs = _analyzer.analyze_file(alarm_file, content)
    lines = content.split("\n")

    ctx.record_step("noted_file", {})

    # ── 步骤1：告警行附近的组件实例化参数块 ──────────────────────
    inst_start, inst_end = _find_call_block(lines, alarm_line - 1)
    ctx.record_step("noted_snippet", {})

    # ── 步骤2：定位父组件，提取属性声明区域 ─────────────────────
    parent_comp = fs.component_at_line(alarm_line)
    if not parent_comp:
        return

    # 属性声明区：struct 声明后到第一个方法体之间
    first_method_line = (
        min(m.line_start for m in parent_comp.methods)
        if parent_comp.methods else parent_comp.line_end
    )
    prop_area_end = min(first_method_line - 1, parent_comp.line_start + 30)
    if prop_area_end > parent_comp.line_start:
        ctx.record_step("noted_snippet", {})

    # ── 步骤3：aboutToReuse / aboutToAppear（预计算位置）───────
    for lc_name in ("aboutToReuse", "aboutToAppear"):
        lc_m = parent_comp.get_method(lc_name)
        if lc_m:
            ctx.record_step("noted_snippet", {})
            break   # 优先 aboutToReuse，有了就不再加 aboutToAppear


# ---------------------------------------------------------------------------
# Slicer 3: hp-arkui-replace-nested-reusable-component-by-builder
# ---------------------------------------------------------------------------

def _slice_replace_nested_by_builder(ctx: GWToolContext) -> None:
    """
    策略：
    1. 找告警行所在的 @Reusable 组件
    2. 提取其 build 方法（含嵌套自定义组件调用）
    3. 提取 @Builder 方法区（若已有 @Builder，作为参考）
    4. 提取组件完整定义（CP 需要完整结构来改造）
    """
    alarm_file = ctx.alarm.file
    alarm_line = ctx.alarm.line_start
    content = _read_file(ctx, alarm_file)
    if not content:
        return

    fs = _analyzer.analyze_file(alarm_file, content)

    ctx.record_step("noted_file", {})

    # 找告警行所在的 @Reusable 组件
    comp = fs.component_at_line(alarm_line)
    if not comp:
        comp = next((c for c in fs.components if c.has_reusable), None)
    if not comp:
        # 退化：只加告警行
        _add_alarm_span(ctx, alarm_file, alarm_line)
        return

    # 整个组件定义（CP 需要完整结构）
    ctx.record_step("noted_snippet", {})


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _read_file(ctx: GWToolContext, file: str) -> str | None:
    root = ctx.repo_path
    if root is None:
        return None
    path = (root / file).resolve()
    try:
        if not path.is_relative_to(root) or not path.is_file():
            return None
    except ValueError:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _find_call_block(lines: list[str], zero_idx: int) -> tuple[int, int]:
    """
    从 zero_idx 行往下找平衡括号的结束行，返回 (start_1based, end_1based)。
    start 往上最多扩展 3 行以包含调用链前缀。
    """
    # 往上找起始的开括号行（避免截断链式调用头）
    start = max(0, zero_idx - 3)
    for i in range(zero_idx, start - 1, -1):
        if "(" in lines[i]:
            start = i
            break

    depth = 0
    seen_open = False
    end = zero_idx
    for i in range(start, len(lines)):
        depth += lines[i].count("(") - lines[i].count(")")
        depth += lines[i].count("{") - lines[i].count("}")
        if "(" in lines[i] or "{" in lines[i]:
            seen_open = True
        if seen_open and depth <= 0:
            end = i
            break

    return start + 1, end + 1   # 1-based


def _extract_instantiated_component(block_text: str) -> str | None:
    """
    从 LazyForEach itemBuilder 块中提取被实例化的自定义组件名。
    模式：  ComponentName({ ... }) 或  ComponentName( ... )
    自定义组件名以大写字母开头。
    """
    # 找形如 ComponentName({ 或 ComponentName( 的调用（大写开头）
    m = re.search(r'\b([A-Z]\w+)\s*\(\s*\{', block_text)
    if m:
        return m.group(1)
    m = re.search(r'\b([A-Z]\w+)\s*\(', block_text)
    if m and m.group(1) not in {"LazyForEach", "ForEach", "JSON", "Swiper", "Stack",
                                  "Column", "Row", "Text", "Image", "Button"}:
        return m.group(1)
    return None


def _find_component_file(ctx: GWToolContext, component_name: str) -> str | None:
    """
    在依赖图邻居文件中找包含 component_name 的 struct 定义文件。
    """
    graph = ctx.sliced_graph
    files = graph.get("files", [])
    root = ctx.repo_path
    if root is None:
        return None

    for file_node in files:
        path = file_node.get("path", "")
        if not path or path == ctx.alarm.file:
            continue
        full = (root / path).resolve()
        try:
            if not full.is_relative_to(root) or not full.is_file():
                continue
        except ValueError:
            continue
        # 快速检查：文件名是否包含组件名（大多数情况下文件名与组件名一致）
        if component_name.lower() in Path(path).stem.lower():
            content = full.read_text(encoding="utf-8", errors="replace")
            if re.search(rf'\bstruct\s+{re.escape(component_name)}\b', content):
                return path
        # 精确检查内容
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            if re.search(rf'\bstruct\s+{re.escape(component_name)}\b', content):
                return path
        except Exception:
            continue
    return None


def _add_alarm_component_snippet(
    ctx: GWToolContext,
    fs,
    lines: list[str],
    alarm_file: str,
    alarm_line: int,
) -> None:
    """退化：提取告警行所在组件的 build 方法"""
    ctx.record_step("noted_file", {})
    comp = fs.component_at_line(alarm_line)
    if comp:
        build_m = comp.get_method("build")
        if build_m:
            ctx.record_step("noted_snippet", {})
    else:
        _add_alarm_span(ctx, alarm_file, alarm_line)


def _add_alarm_span(ctx: GWToolContext, alarm_file: str, alarm_line: int) -> None:
    alarm = ctx.alarm
    ctx.record_step("noted_snippet", {})


def _build_has_conditional(content: str, build_method) -> bool:
    """
    检测 build() 方法体内是否含有 if/else 分支。
    若 @Reusable 组件的 build 方法含条件渲染，ArkUI 的
    hp-arkui-suggest-reuseid-for-if-else-reusable-component 规则要求
    LazyForEach 调用点添加 .reuseId()。
    """
    lines = content.split("\n")
    # 截取 build 方法体的代码行（零索引）
    start_idx = max(0, build_method.line_start - 1)
    end_idx = min(len(lines), build_method.line_end)
    body_lines = lines[start_idx:end_idx]
    body_text = "\n".join(body_lines)
    # 检测 if ( 或 if( 语句（排除注释行）
    return bool(re.search(r'\bif\s*\(', body_text))


# ---------------------------------------------------------------------------
# 通用 ArkTS 兜底层（P0 扩展）
# ---------------------------------------------------------------------------

# 规则族与需要补充的结构的映射
# 含义：对这些规则，除了告警行还必须提取的额外结构
_RULE_NEEDS_METHOD_BODY = frozenset({
    "@performance/hp-arkui-no-state-var-access-in-loop",
    "@performance/hp-arkui-use-local-var-to-replace-state-var",
    "@performance/high-frequency-log-check",
})
_RULE_NEEDS_LAZY_FOREACH = frozenset({
    "@performance/hp-arkui-set-cache-count-for-lazyforeach-grid",
    "@performance/hp-arkui-no-stringify-in-lazyforeach-key-generator",
    "@performance/foreach-args-check",
})
_RULE_NEEDS_PROP_AREA = frozenset({
    "@performance/hp-arkui-no-state-var-access-in-loop",
    "@performance/hp-arkui-use-local-var-to-replace-state-var",
    "@performance/hp-arkui-use-object-link-to-replace-prop",
    "@performance/hp-arkui-use-object-link-to-replace-prop",
})
# 告警行附近存在 LazyForEach 的搜索窗口（行数）
_LAZY_FOREACH_WINDOW = 25
# 方法体大小上限（超过则截断，避免把整个 build() 方法塞给 CP）
_METHOD_BODY_MAX_LINES = 80


def run_arkts_generic_slicer(ctx: GWToolContext) -> bool:
    """
    ArkTS 通用兜底层：对所有 .ets 告警文件运行，
    无论是否已有规则特化 Slicer。

    策略：
      1. 告警行 + 扩展上下文（固定）
      2. 告警行所在方法体（适用于循环/状态访问类规则）
      3. 组件属性声明区（适用于 @State/@Prop 类规则）
      4. LazyForEach 调用块（适用于迭代器类规则）
      5. 相关邻居文件 BFS 召回（所有规则）

    Returns:
        True 如果文件是 .ets/.ts 且成功处理，否则 False
    """
    alarm_file = ctx.alarm.file
    if not (alarm_file.endswith(".ets") or alarm_file.endswith(".ts")):
        return False

    content = _read_file(ctx, alarm_file)
    if not content:
        return False

    fs = _analyzer.analyze_file(alarm_file, content)
    lines = content.split("\n")
    alarm_line = ctx.alarm.line_start
    rule = ctx.alarm.rule

    ctx.record_step("noted_file", {})

    # ── 步骤1：告警行（扩展上下文，±3行）───────────────────────
    ctx_start = max(1, alarm_line - 3)
    ctx_end   = min(len(lines), ctx.alarm.line_end + 3)
    ctx.record_step("noted_snippet", {})

    comp = fs.component_at_line(alarm_line)

    # ── 步骤2：告警行所在方法体 ──────────────────────────────────
    if rule in _RULE_NEEDS_METHOD_BODY or rule.startswith("@performance/hp-arkui-"):
        enclosing = _find_enclosing_method(comp, alarm_line) if comp else None
        if enclosing and enclosing.name != "build":
            # build() 方法体通常很大（>100行），对大多数规则意义不大
            body_size = enclosing.line_end - enclosing.line_start + 1
            body_end = min(enclosing.line_end, enclosing.line_start + _METHOD_BODY_MAX_LINES - 1)
            ctx.record_step("noted_snippet", {})

    # ── 步骤3：组件属性声明区 ────────────────────────────────────
    if (rule in _RULE_NEEDS_PROP_AREA or rule.startswith("@performance/hp-arkui-")) and comp:
        prop_end = _prop_area_end(comp)
        if prop_end > comp.line_start:
            ctx.record_step("noted_snippet", {})

    # ── 步骤4：LazyForEach 调用块 ─────────────────────────────────
    if rule in _RULE_NEEDS_LAZY_FOREACH or "lazyforeach" in rule.lower() or "foreach" in rule.lower():
        nearby_lazy = [
            l for l in fs.lazy_foreach_lines
            if abs(l - alarm_line) <= _LAZY_FOREACH_WINDOW
        ]
        if not nearby_lazy:
            nearby_lazy = fs.lazy_foreach_lines[:1]   # 文件内任意一处
        for lf_line in nearby_lazy[:2]:
            lf_start, lf_end = _find_call_block(lines, lf_line - 1)
            ctx.record_step("noted_snippet", {})

    # ── 步骤5：BFS 邻居文件召回 ───────────────────────────────────
    graph = ctx.sliced_graph
    for file_node in graph.get("files", []):
        path = file_node.get("path", "")
        if path and path != alarm_file:
            ctx.record_step("noted_file", {})

    ctx.record_step("arkts_generic_slicer", {
        "rule": rule,
        "alarm_file": alarm_file,
        "comp_found": comp.name if comp else None,
        "snippets_added": 0,
    })
    return True


def _find_enclosing_method(comp, alarm_line: int):
    """返回包含 alarm_line 的最内层方法（不含 build）"""
    candidates = [
        m for m in comp.methods
        if m.line_start <= alarm_line <= m.line_end and m.name != "build"
    ]
    if not candidates:
        return None
    # 选最小范围（最内层）
    return min(candidates, key=lambda m: m.line_end - m.line_start)


def _prop_area_end(comp) -> int:
    """
    组件属性声明区结束行：struct 声明行到第一个方法体起始行之间，
    最多延伸 50 行，避免把整个组件头部都纳入。
    """
    first_method_line = (
        min(m.line_start for m in comp.methods)
        if comp.methods else comp.line_end
    )
    return min(first_method_line - 1, comp.line_start + 50)
