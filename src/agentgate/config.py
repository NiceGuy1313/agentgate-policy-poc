from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def policies_config() -> dict:
    return load_yaml("policies.yaml")


def named_source_rules() -> dict:
    return load_yaml("named_source_rules.yaml")


def sensitive_reads() -> dict[str, list[str]]:
    return load_yaml("sensitive_reads.yaml")


def sensitive_categories_of(tool: str) -> list[str]:
    return [cat for cat, tools in sensitive_reads().items() if tool in tools]


def effect_tools() -> set[str]:
    roles = load_yaml("tool_roles.yaml")
    return {t for tools in roles["effect"].values() for t in tools}
