"""공통 경로, YAML 설정 loader, `.env` loader를 제공한다.

Gate 설정(config/gate.yaml), 룰북(config/rulebook.yaml, config/tool_args.yaml)은 각 모듈이 읽는다. 여기에는
AgentDojo 툴 환경 바인딩(config/agentdojo/) loader만 둔다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@lru_cache(maxsize=None)
def load_config_yaml(name: str) -> dict:
    """`name` is relative to config/, e.g. "gate.yaml"."""
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def load_named_source_rules() -> dict:
    return load_config_yaml("agentdojo/named_source_rules.yaml")


def load_sensitive_read_categories() -> dict[str, list[str]]:
    return load_config_yaml("agentdojo/sensitive_reads.yaml")


def sensitive_categories_for_tool(tool: str) -> list[str]:
    return [category for category, tools in load_sensitive_read_categories().items() if tool in tools]


def load_effect_tools() -> set[str]:
    roles = load_config_yaml("agentdojo/tool_roles.yaml")
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


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temporary file + rename, so a concurrent reader never sees a half-written cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{id(text)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def require_env_vars(*names: str) -> dict[str, str]:
    load_dotenv()
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)} (set them in .env, see .env.example)")
    return {name: os.environ[name] for name in names}
