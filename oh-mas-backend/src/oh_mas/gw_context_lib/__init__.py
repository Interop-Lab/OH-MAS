"""
GW Context Mode Library - 规则修复上下文构建模式库

GW Agent通过 get_context_mode(rule_id) 获取对应规则的修复模式配置。
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import TypedDict


ALLOWED_SLICE_STRATEGIES = {
    "minimal",
    "component_aware",
    "decorator_aware",
    "dataflow_aware",
    "control_flow_aware",
    "scope_aware",
    "type_aware",
    "call_graph_aware",
    "api_aware",
    "import_only",
}


class ContextMode(TypedDict):
    """规则修复上下文模式配置 - GW专注于代码片段和语义信息提取"""
    slice_strategy: str
    semantic_focus: list[str]
    max_depth: int
    description: str
    must_include: list[str]
    snippet_hints: list[str]
    rule_family: str


_CONFIG_PATH = Path(__file__).parent / "rule_context_modes.json"
_cache: dict | None = None


def _load_config() -> dict:
    """加载配置文件（带缓存）"""
    global _cache
    if _cache is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def _normalize_mode(rule_id: str, raw_mode: dict) -> ContextMode:
    """补齐旧配置的兼容字段并做基础校验。"""
    mode = deepcopy(raw_mode)
    mode.setdefault("must_include", ["alarm_file", "target_line_or_span"])
    mode.setdefault("snippet_hints", ["extract_smallest_enclosing_block", "include_directly_relevant_declarations_only"])
    mode.setdefault("rule_family", _infer_rule_family(rule_id))

    # GW no longer uses constraint_focus and anti_patterns - remove if present
    mode.pop("constraint_focus", None)
    mode.pop("anti_patterns", None)

    if mode.get("slice_strategy") not in ALLOWED_SLICE_STRATEGIES:
        raise ValueError(f"Unsupported slice_strategy for {rule_id}: {mode.get('slice_strategy')}")
    if not isinstance(mode.get("max_depth"), int) or mode["max_depth"] < 0:
        raise ValueError(f"Invalid max_depth for {rule_id}: {mode.get('max_depth')}")

    return mode  # type: ignore[return-value]


def _infer_rule_family(rule_id: str) -> str:
    if rule_id.startswith("@hw-stylistic/"):
        return "arkts_style"
    if rule_id.startswith("@performance/hp-arkui-"):
        return "arkui_component_performance"
    if rule_id.startswith("@performance/foreach-"):
        return "arkui_iteration"
    if rule_id.startswith("@performance/high-frequency-"):
        return "arkui_hot_path"
    if rule_id.startswith("@performance/hp-arkts-"):
        return "arkts_type"
    if rule_id.startswith("@previewer/"):
        return "arkts_preview"
    if rule_id.startswith("cppcheck/"):
        return "cppcheck"
    return "default"


def get_context_mode(rule_id: str) -> ContextMode:
    """
    根据规则ID获取上下文构建模式。

    Args:
        rule_id: 告警规则ID，如 "@performance/hp-arkui-prefer-lazyforeach"

    Returns:
        ContextMode配置，包含slice_strategy, semantic_focus, constraint_focus, max_depth, description

    Example:
        >>> mode = get_context_mode("@performance/hp-arkui-prefer-lazyforeach")
        >>> mode["slice_strategy"]
        'component_aware'
    """
    config = _load_config()
    rules = config.get("rules", {})

    # 1. 精确匹配
    if rule_id in rules:
        return _normalize_mode(rule_id, rules[rule_id])

    # 2. 前缀匹配（支持通配符配置如 "@hw-stylistic/*"）
    for pattern, mode in rules.items():
        if pattern.endswith("/*"):
            prefix = pattern[:-1]  # 去掉 "*"
            if rule_id.startswith(prefix):
                return _normalize_mode(rule_id, mode)

    # 3. 返回默认配置
    return _normalize_mode(rule_id, rules.get("_default", {
        "slice_strategy": "import_only",
        "semantic_focus": ["import", "export"],
        "max_depth": 1,
        "description": "未知规则，使用默认配置",
        "must_include": ["alarm_file", "target_line"],
        "snippet_hints": ["focus_on_alarm_span", "extract_smallest_enclosing_block"],
        "rule_family": "default",
    }))


def list_rules() -> list[str]:
    """列出所有已配置的规则ID"""
    config = _load_config()
    rules = config.get("rules", {})
    return [k for k in rules.keys() if not k.startswith("=") and not k.startswith("_")]
