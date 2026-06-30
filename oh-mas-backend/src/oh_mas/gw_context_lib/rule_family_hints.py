"""
Rule Family Hints for GW Agent

This module provides specialized localization strategies for different ArkUI linter rule families.
These hints are loaded from a YAML configuration file and guide the GW agent to precisely
locate violations and extract focused context.
"""

import yaml
from pathlib import Path
from typing import Any

# Path to the hints configuration file
HINTS_CONFIG_PATH = Path(__file__).parent / "rule_family_hints.yaml"

# Cache for loaded hints
_hints_cache: dict[str, Any] | None = None


def load_hints() -> dict[str, Any]:
    """Load rule family hints from YAML configuration file.

    Returns:
        Dictionary of rule hints keyed by rule ID
    """
    global _hints_cache

    if _hints_cache is not None:
        return _hints_cache

    try:
        with open(HINTS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        # Remove 'config' section and keep only rule hints
        if isinstance(data, dict):
            data.pop('config', None)
            _hints_cache = data
        else:
            _hints_cache = {}

        return _hints_cache
    except Exception:
        # Fail gracefully if config doesn't exist or is invalid
        return {}


def get_rule_family_hint(rule_id: str) -> dict[str, Any] | None:
    """
    Get localization hint for a specific rule.

    Args:
        rule_id: Full rule ID (e.g., "@performance/hp-arkui-no-func-as-arg-for-reusable-component")

    Returns:
        Hint dictionary or None if no hint available
    """
    hints = load_hints()
    return hints.get(rule_id)


def get_all_hints() -> dict[str, Any]:
    """Get all available rule family hints."""
    return load_hints()


def reload_hints() -> None:
    """Force reload hints from file. Useful for testing or when config is modified."""
    global _hints_cache
    _hints_cache = None
