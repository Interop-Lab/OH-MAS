"""Rule-specific instruction loader for CP Worker.

This module provides dynamic loading of rule-specific repair instructions
based on the alarm rule pattern. Instructions are stored in YAML files
and loaded on-demand to avoid polluting the base prompt with unused context.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class RuleInstructionLoader:
    """Loads rule-specific instructions from YAML configuration files."""

    def __init__(self, rules_dir: Path | None = None):
        """Initialize loader with rules directory.

        Args:
            rules_dir: Path to directory containing rule YAML files.
                      Defaults to cp_worker_rules/ in this package.
        """
        if rules_dir is None:
            rules_dir = Path(__file__).parent
        self.rules_dir = Path(rules_dir)
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_all_rules()

    def _load_all_rules(self) -> None:
        """Load all rule configuration files into memory cache."""
        if not self.rules_dir.exists():
            return

        for yaml_file in self.rules_dir.glob("*.yaml"):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    if config and "rule_pattern" in config:
                        rule_id = yaml_file.stem
                        self._cache[rule_id] = config
            except Exception:
                # Graceful degradation: skip malformed files
                continue

    def get_instructions(self, alarm_rule: str) -> str:
        """Get rule-specific instructions for given alarm rule.

        Args:
            alarm_rule: The linter rule ID (e.g., "@performance/hp-arkui-use-reusable-component")

        Returns:
            Formatted instructions string, or empty string if no match found.
        """
        matched_configs = []

        for rule_id, config in self._cache.items():
            pattern = config.get("rule_pattern", "")
            priority = config.get("priority", 0)

            # Check pattern match
            if self._rule_matches(alarm_rule, pattern):
                matched_configs.append((priority, config))

        if not matched_configs:
            return ""

        # Sort by priority (higher priority first), return highest match
        matched_configs.sort(key=lambda x: x[0], reverse=True)
        best_config = matched_configs[0][1]

        instructions = best_config.get("instructions", "").strip()
        if not instructions:
            return ""

        # Format as a distinct section
        header = "# Rule-Specific Repair Instructions\n\n"
        return header + instructions

    def _rule_matches(self, alarm_rule: str, pattern: str) -> bool:
        """Check if alarm rule matches the given pattern.

        Supports:
        - Exact match: "no-lifecycle-misuse"
        - Regex pattern: "@performance/hp-arkui-.*-reusable"
        - Wildcard: ".*"
        - Explicit list in config: checks applies_to field
        """
        try:
            return bool(re.search(pattern, alarm_rule))
        except re.error:
            # Invalid regex, fall back to exact match
            return alarm_rule == pattern


# Global singleton instance
_loader: RuleInstructionLoader | None = None


def get_rule_instructions(alarm_rule: str) -> str:
    """Get rule-specific instructions for given alarm rule (singleton API).

    Args:
        alarm_rule: The linter rule ID

    Returns:
        Formatted instructions string
    """
    global _loader
    if _loader is None:
        _loader = RuleInstructionLoader()
    return _loader.get_instructions(alarm_rule)
