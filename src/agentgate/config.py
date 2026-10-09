"""프로젝트 경로, YAML 설정 loader, `.env` loader를 제공한다."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@lru_cache(maxsize=None)
def load_config_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def load_policy_config() -> dict:
    return load_config_yaml("policies.yaml")


def load_named_source_rules() -> dict:
    return load_config_yaml("named_source_rules.yaml")


def load_sensitive_read_categories() -> dict[str, list[str]]:
    return load_config_yaml("sensitive_reads.yaml")


def sensitive_categories_for_tool(tool: str) -> list[str]:
    return [category for category, tools in load_sensitive_read_categories().items() if tool in tools]


def load_effect_tools() -> set[str]:
    roles = load_config_yaml("tool_roles.yaml")
    return {tool for tools in roles["effect"].values() for tool in tools}


def load_dotenv(path: Path | None = None) -> None:
    """Minimal `.env` reader; variables already set in the environment win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value


def require_env_vars(*names: str) -> dict[str, str]:
    load_dotenv()
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)} (set them in .env, see .env.example)")
    return {name: os.environ[name] for name in names}
