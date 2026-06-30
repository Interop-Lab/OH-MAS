"""
GW 语义工具 (P1)

基于 ArkTSAnalyzer 提供单文件结构查询能力，供 LLM 工具循环和
确定性 Slicer 两条路径共同使用。
"""

from __future__ import annotations

from oh_mas.agents.gw_tools.context import GWToolContext
from oh_mas.gw_semantic_lib import ArkTSAnalyzer


_analyzer = ArkTSAnalyzer()


def analyze_component(
    ctx: GWToolContext,
    *,
    file: str,
    component_name: str | None = None,
) -> dict:
    """
    解析 .ets 文件，返回组件结构信息（struct 名称、装饰器、方法、属性）。

    Args:
        file: 相对于 repo_root 的文件路径
        component_name: 指定组件名（可选，不指定则返回文件内所有组件）

    Returns:
        {
            "file": str,
            "components": [...],   # 匹配到的组件列表
            "lazy_foreach_lines": [...],
            "foreach_lines": [...],
            "top_builders": [...],
        }
    """
    content = _read(ctx, file)
    if content is None:
        output: dict = {"file": file, "error": "file_not_found", "components": []}
        return ctx.record_step("analyze_component", output, file=file)

    fs = _analyzer.analyze_file(file, content)

    if component_name:
        comps = [c for c in fs.components if c.name == component_name]
    else:
        comps = fs.components

    output = {
        "file": file,
        "components": [
            {
                "name": c.name,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "decorators": c.decorators,
                "has_reusable": c.has_reusable,
                "methods": [
                    {
                        "name": m.name,
                        "kind": m.kind,
                        "line_start": m.line_start,
                        "line_end": m.line_end,
                        "decorator": m.decorator,
                        "is_async": m.is_async,
                    }
                    for m in c.methods
                ],
                "props": [
                    {"name": p.name, "line": p.line, "decorator": p.decorator}
                    for p in c.props
                ],
            }
            for c in comps
        ],
        "lazy_foreach_lines": fs.lazy_foreach_lines,
        "foreach_lines": fs.foreach_lines,
        "top_builders": fs.top_builders,
        "total_components": len(fs.components),
    }
    return ctx.record_step("analyze_component", output, file=file, component_name=component_name)


def find_component_at_line(
    ctx: GWToolContext,
    *,
    file: str,
    line: int,
) -> dict:
    """
    返回包含指定行号的组件信息（用于定位告警所在组件）。
    """
    content = _read(ctx, file)
    if content is None:
        output = {"file": file, "line": line, "error": "file_not_found", "component": None}
        return ctx.record_step("find_component_at_line", output, file=file, line=line)

    fs = _analyzer.analyze_file(file, content)
    comp = fs.component_at_line(line)

    output: dict = {"file": file, "line": line}
    if comp:
        output["component"] = {
            "name": comp.name,
            "line_start": comp.line_start,
            "line_end": comp.line_end,
            "decorators": comp.decorators,
            "has_reusable": comp.has_reusable,
            "lifecycle_methods": [
                {"name": m.name, "line_start": m.line_start, "line_end": m.line_end}
                for m in comp.get_lifecycle_methods()
            ],
            "builder_methods": [
                {"name": m.name, "line_start": m.line_start, "line_end": m.line_end}
                for m in comp.get_builder_methods()
            ],
            "props": [
                {"name": p.name, "line": p.line, "decorator": p.decorator}
                for p in comp.props
            ],
        }
    else:
        output["component"] = None
        output["note"] = "no component contains this line"

    return ctx.record_step("find_component_at_line", output, file=file, line=line)


# ---------------------------------------------------------------------------
# 内部帮助函数
# ---------------------------------------------------------------------------

def _read(ctx: GWToolContext, file: str) -> str | None:
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
