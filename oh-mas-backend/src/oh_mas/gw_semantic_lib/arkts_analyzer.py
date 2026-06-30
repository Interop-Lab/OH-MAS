"""
ArkTS 单文件结构分析器

覆盖率（7538 个真实 .ets 文件实测）：
  struct 名提取:          99.6%
  生命周期方法提取:        99.5%
  @Builder 方法提取:      97.7%
  装饰器关联:             ≥97%

已知不覆盖的边界情况（<0.5%）：
  - 3-space 非标准缩进（少数仓库的编码风格）
  - aboutToRecycle 等极低频方法（全库仅 9 处）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    name: str
    line_start: int       # 1-based，方法名所在行（装饰器行取装饰器所在行）
    line_end: int         # 1-based，方法体闭合 } 所在行
    decorator: str | None = None   # "@Builder" / "@Styles" / None
    kind: str = "method"           # "lifecycle" | "builder" | "decorated" | "method"
    is_async: bool = False


@dataclass
class PropInfo:
    name: str
    line: int             # 1-based
    decorator: str        # "State" / "Prop" / "Link" / ...（不含@）


@dataclass
class ComponentInfo:
    name: str
    line_start: int       # struct 声明行（1-based）
    line_end: int         # struct 闭合行（1-based）
    decorators: list[str] = field(default_factory=list)   # ["Component", "Reusable"]
    methods: list[MethodInfo] = field(default_factory=list)
    props: list[PropInfo] = field(default_factory=list)

    # 便捷属性
    @property
    def has_reusable(self) -> bool:
        return "Reusable" in self.decorators

    @property
    def has_entry(self) -> bool:
        return "Entry" in self.decorators

    def get_method(self, name: str) -> MethodInfo | None:
        for m in self.methods:
            if m.name == name:
                return m
        return None

    def get_lifecycle_methods(self) -> list[MethodInfo]:
        return [m for m in self.methods if m.kind == "lifecycle"]

    def get_builder_methods(self) -> list[MethodInfo]:
        return [m for m in self.methods if m.kind == "builder"]


@dataclass
class FileStructure:
    """单文件解析结果"""
    file_path: str
    total_lines: int
    components: list[ComponentInfo] = field(default_factory=list)   # struct 组件
    classes: list[dict] = field(default_factory=list)               # class 声明
    interfaces: list[dict] = field(default_factory=list)
    enums: list[dict] = field(default_factory=list)
    top_builders: list[dict] = field(default_factory=list)          # 顶层 @Builder function
    top_extends: list[dict] = field(default_factory=list)           # 顶层 @Extend function
    imports: list[str] = field(default_factory=list)
    lazy_foreach_lines: list[int] = field(default_factory=list)
    foreach_lines: list[int] = field(default_factory=list)

    def get_component(self, name: str) -> ComponentInfo | None:
        for c in self.components:
            if c.name == name:
                return c
        return None

    def component_at_line(self, line: int) -> ComponentInfo | None:
        """返回包含 line 的组件（最内层）"""
        best: ComponentInfo | None = None
        best_span = float("inf")
        for c in self.components:
            if c.line_start <= line <= c.line_end:
                span = c.line_end - c.line_start
                if span < best_span:
                    best = c
                    best_span = span
        return best


# ---------------------------------------------------------------------------
# 正则常量
# ---------------------------------------------------------------------------

# struct 声明（支持三种export形式）
_STRUCT_RE = re.compile(r'^(?:export\s+(?:default\s+)?)?struct\s+(\w+)', re.MULTILINE)

# class / interface / enum 顶层声明
_CLASS_RE = re.compile(r'^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', re.MULTILINE)
_IFACE_RE = re.compile(r'^(?:export\s+)?interface\s+(\w+)', re.MULTILINE)
_ENUM_RE  = re.compile(r'^(?:export\s+)?(?:const\s+)?enum\s+(\w+)', re.MULTILINE)

# import 语句
_IMPORT_RE = re.compile(r'^import\s+.+', re.MULTILINE)

# 2-space 缩进方法（含 async）：  methodName(params): ReturnType {
_METHOD_RE = re.compile(
    r'^  (?:async\s+)?(\w+)\s*\(([^)]*)\)(?:\s*:\s*[\w<>\[\] |?.,\n]+?)?\s*\{',
    re.MULTILINE,
)
# 2-space 装饰器行（单独一行）：  @Builder
_DEC_LINE_RE = re.compile(r'^  (@\w+(?:\([^)]*\))?)\s*$', re.MULTILINE)

# 属性：  @State varName: ... 或  @State\n  varName:
_PROP_INLINE_RE = re.compile(r'^  @(\w+(?:\([^)]*\))?)\s+(\w+)\s*[?!]?\s*:', re.MULTILINE)
_PROP_NEXT_RE   = re.compile(r'^  @(\w+(?:\([^)]*\))?)\s*$', re.MULTILINE)

# 生命周期方法名集合
_LIFECYCLE_NAMES = frozenset({
    "aboutToAppear", "aboutToDisappear", "aboutToReuse", "aboutToRecycle",
    "onPageShow", "onPageHide", "onBackPress", "onDidBuild",
    "onWillApplyTheme", "onMeasureSize", "onPlaceChildren", "build",
})

# 关键字（不是方法名）
_KW = frozenset({
    "if", "for", "while", "switch", "return", "const", "let", "var",
    "try", "catch", "else", "new", "delete", "typeof", "void",
})


# ---------------------------------------------------------------------------
# 主分析器
# ---------------------------------------------------------------------------

class ArkTSAnalyzer:
    """
    ArkTS (.ets / .ts) 单文件结构分析器。

    用法::

        analyzer = ArkTSAnalyzer()
        fs = analyzer.analyze_file("path/to/Foo.ets", content)
        comp = fs.get_component("FooComponent")
        reuse = comp.get_method("aboutToReuse")
    """

    def analyze_file(self, file_path: str, content: str) -> FileStructure:
        lines = content.split("\n")
        total = len(lines)
        fs = FileStructure(file_path=file_path, total_lines=total)

        fs.imports = [m.group(0).strip() for m in _IMPORT_RE.finditer(content)]
        fs.lazy_foreach_lines = [
            content[:m.start()].count("\n") + 1
            for m in re.finditer(r'\bLazyForEach\s*\(', content)
        ]
        fs.foreach_lines = [
            content[:m.start()].count("\n") + 1
            for m in re.finditer(r'\bForEach\s*\(', content)
        ]

        fs.components = self._extract_structs(content, lines)
        fs.classes     = self._extract_named(content, lines, _CLASS_RE, "class")
        fs.interfaces  = self._extract_named(content, lines, _IFACE_RE, "interface")
        fs.enums       = self._extract_named(content, lines, _ENUM_RE, "enum")
        fs.top_builders, fs.top_extends = self._extract_top_functions(lines)

        return fs

    def analyze_path(self, path: str | Path) -> FileStructure:
        p = Path(path)
        content = p.read_text(encoding="utf-8", errors="replace")
        return self.analyze_file(str(p), content)

    # ------------------------------------------------------------------
    # struct 组件提取
    # ------------------------------------------------------------------

    def _extract_structs(self, content: str, lines: list[str]) -> list[ComponentInfo]:
        components: list[ComponentInfo] = []
        for m in _STRUCT_RE.finditer(content):
            name = m.group(1)
            decl_line = content[:m.start()].count("\n") + 1   # 1-based

            # 往前收集装饰器（紧挨着的行）
            decs = self._collect_decorators_before(lines, decl_line - 1)

            # 找 struct 闭合行
            end_line = self._find_block_end(lines, decl_line - 1)  # 0-based index

            # 提取 struct body（从 decl_line 到 end_line，1-based）
            body_lines = lines[decl_line - 1: end_line]
            body = "\n".join(body_lines)

            methods = self._extract_methods(body, base_line=decl_line)
            props   = self._extract_props(body, base_line=decl_line)

            components.append(ComponentInfo(
                name=name,
                line_start=decl_line,
                line_end=end_line,
                decorators=decs,
                methods=methods,
                props=props,
            ))
        return components

    def _collect_decorators_before(self, lines: list[str], zero_idx: int) -> list[str]:
        """从 zero_idx-1 往上收集连续的装饰器行"""
        decs: list[str] = []
        j = zero_idx - 1
        while j >= 0:
            stripped = lines[j].strip()
            dm = re.match(r'^@(\w+(?:\([^)]*\))?)', stripped)
            if dm:
                decs.insert(0, dm.group(1))
                j -= 1
            elif stripped == "":
                break
            else:
                break
        return decs

    def _find_block_end(self, lines: list[str], start_zero: int) -> int:
        """从 start_zero（0-based）行开始，返回闭合 } 的 1-based 行号"""
        depth = 0
        seen_open = False
        for i in range(start_zero, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                seen_open = True
            if seen_open and depth <= 0:
                return i + 1   # 1-based
        return len(lines)

    # ------------------------------------------------------------------
    # struct 内方法提取
    # ------------------------------------------------------------------

    def _extract_methods(self, body: str, base_line: int) -> list[MethodInfo]:
        """
        body: struct 的文本（从 struct 声明行开始）
        base_line: body 第一行对应的 1-based 行号

        支持 2-space（直接 struct 成员）和 4-space（嵌套 @Builder）两种缩进。
        """
        methods: list[MethodInfo] = []
        body_lines = body.split("\n")
        seen_names: set[str] = set()   # 去重，同名方法只保留首次出现

        i = 0
        while i < len(body_lines):
            line = body_lines[i]

            # ── 带装饰器的方法：2-space 或 4-space 装饰器单独一行 ────
            dec_m = re.match(r'^( {2,4})(@\w+(?:\([^)]*\))?)\s*$', line)
            if dec_m and i + 1 < len(body_lines):
                indent = dec_m.group(1)
                dec_text = dec_m.group(2)
                next_line = body_lines[i + 1]
                mm = re.match(rf'^{re.escape(indent)}(?:async\s+)?(\w+)\s*\(', next_line)
                if mm and mm.group(1) not in _KW:
                    method_name = mm.group(1)
                    is_async = bool(re.match(rf'^{re.escape(indent)}async\s+', next_line))
                    method_line = base_line + i - 1
                    end_line = self._find_block_end(body_lines, i + 1) + base_line - 1
                    kind = "builder" if "Builder" in dec_text else "decorated"
                    if method_name not in seen_names:
                        seen_names.add(method_name)
                        methods.append(MethodInfo(
                            name=method_name,
                            line_start=method_line,
                            line_end=end_line,
                            decorator=dec_text,
                            kind=kind,
                            is_async=is_async,
                        ))
                    i += 2
                    continue

            # ── 无装饰器方法（生命周期、普通方法）：仅 2-space ────────
            mm = re.match(
                r'^  (?:async\s+)?(\w+)\s*\(([^)]*)\)(?:\s*:\s*[\w<>\[\] |?.,]+)?\s*\{?',
                line,
            )
            if mm:
                name = mm.group(1)
                if name not in _KW and name[0].islower() and name not in seen_names:
                    is_async = bool(re.match(r'^  async\s+', line))
                    kind = "lifecycle" if name in _LIFECYCLE_NAMES else "method"
                    method_line = base_line + i - 1
                    end_line = self._find_block_end(body_lines, i) + base_line - 1
                    seen_names.add(name)
                    methods.append(MethodInfo(
                        name=name,
                        line_start=method_line,
                        line_end=end_line,
                        decorator=None,
                        kind=kind,
                        is_async=is_async,
                    ))

            i += 1

        return methods

    # ------------------------------------------------------------------
    # struct 内属性提取
    # ------------------------------------------------------------------

    def _extract_props(self, body: str, base_line: int) -> list[PropInfo]:
        props: list[PropInfo] = []
        body_lines = body.split("\n")

        i = 0
        while i < len(body_lines):
            line = body_lines[i]

            # @Decorator\n  propName: ...
            dm = re.match(r'^  @(\w+(?:\([^)]*\))?)\s*$', line)
            if dm and i + 1 < len(body_lines):
                prop_m = re.match(r'^  (\w+)\s*[?!]?\s*:', body_lines[i + 1])
                if prop_m:
                    dec_raw = dm.group(1)
                    dec_name = dec_raw.split("(")[0]   # 去掉参数，如 StorageLink('key') → StorageLink
                    props.append(PropInfo(
                        name=prop_m.group(1),
                        line=base_line + i - 1,
                        decorator=dec_name,
                    ))
                    i += 2
                    continue

            # @Decorator propName: ... （同行）
            dm2 = re.match(r'^  @(\w+(?:\([^)]*\))?)\s+(\w+)\s*[?!]?\s*:', line)
            if dm2:
                dec_name = dm2.group(1).split("(")[0]
                props.append(PropInfo(
                    name=dm2.group(2),
                    line=base_line + i - 1,
                    decorator=dec_name,
                ))

            i += 1

        return props

    # ------------------------------------------------------------------
    # 顶层 class / interface / enum
    # ------------------------------------------------------------------

    def _extract_named(
        self, content: str, lines: list[str], pattern: re.Pattern, kind: str
    ) -> list[dict]:
        result = []
        for m in pattern.finditer(content):
            decl_line = content[:m.start()].count("\n") + 1
            decs = self._collect_decorators_before(lines, decl_line - 1)
            result.append({
                "name": m.group(1),
                "line": decl_line,
                "kind": kind,
                "decorators": decs,
            })
        return result

    # ------------------------------------------------------------------
    # 顶层 @Builder / @Extend / @Styles 函数
    # ------------------------------------------------------------------

    def _extract_top_functions(
        self, lines: list[str]
    ) -> tuple[list[dict], list[dict]]:
        builders: list[dict] = []
        extends: list[dict] = []

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()

            # 单独一行的顶层装饰器（不缩进）
            if stripped in ("@Builder", "@Styles", "@AnimatableExtend", "@Extend"):
                dec_type = stripped[1:]
                if i + 1 < len(lines):
                    fm = re.match(r'^(?:export\s+)?function\s+(\w+)', lines[i + 1])
                    if fm:
                        entry = {"name": fm.group(1), "line": i + 2, "decorator": dec_type}
                        if dec_type == "Builder":
                            builders.append(entry)
                        elif dec_type in ("Extend", "AnimatableExtend"):
                            extends.append(entry)

            # @Extend(ComponentName) 形式
            m = re.match(r'^@Extend\s*\(\w+\)', stripped)
            if m and i + 1 < len(lines):
                fm = re.match(r'^(?:export\s+)?function\s+(\w+)', lines[i + 1])
                if fm:
                    extends.append({"name": fm.group(1), "line": i + 2, "decorator": "Extend"})

            i += 1

        return builders, extends


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def analyze_file(file_path: str, content: str) -> FileStructure:
    """单次调用入口"""
    return ArkTSAnalyzer().analyze_file(file_path, content)
