"""프레임워크 어댑터 등록부: 내장 어댑터, `config/frameworks.yaml`, `agentgate.frameworks` entry point를 모은다.

config/frameworks.yaml
  frameworks:
    <name>:
      adapter: package.module:Class          # Python 어댑터, 또는
      contract:                              # 규약 v1을 따르는 Wrapper(docs/integration_guide.md)
        command: [python, path/to/wrapper.py]
        cwd: path                            # 선택
      python: /path/to/venv/bin/python       # 선택: 이 인터프리터의 subprocess worker에서 실행
      display_name / description            # 선택
"""

from __future__ import annotations

import importlib
import importlib.metadata
from functools import lru_cache

import yaml

from agentgate.config import CONFIG_DIR

BUILTIN = {
    "native": "agentgate.adapters.native:NativeAdapter",
    "smolagents": "agentgate.adapters.smolagents_adapter:SmolagentsAdapter",
    "mini-swe-agent": "agentgate.adapters.mini_swe_agent_adapter:MiniSweAgentAdapter",
}
ENTRY_POINT_GROUP = "agentgate.frameworks"


def _load(target: str):
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def framework_config() -> dict:
    path = CONFIG_DIR / "frameworks.yaml"
    return (yaml.safe_load(path.read_text()) or {}).get("frameworks", {}) if path.exists() else {}


@lru_cache(maxsize=None)
def _entries() -> dict[str, dict]:
    entries = {name: {"adapter": target} for name, target in BUILTIN.items()}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        entries.setdefault(ep.name, {"adapter": ep.value})
    for name, entry in framework_config().items():
        entries[name] = {**entries.get(name, {}), **(entry or {})}
    return entries


def names() -> list[str]:
    return list(_entries())


def entry(name: str) -> dict:
    if name not in _entries():
        raise KeyError(f"unknown framework {name!r} (registered: {', '.join(_entries())})")
    return _entries()[name]


def get(name: str):
    """Adapter instance for `name`."""
    item = entry(name)
    if "contract" in item:
        from agentgate.adapters.contract import ContractAdapter
        adapter = ContractAdapter(name, item["contract"])
    else:
        adapter = _load(item["adapter"])()
        adapter.name = adapter.name or name
    if item.get("display_name"):
        adapter.display_name = item["display_name"]
    if item.get("description"):
        adapter.description = item["description"]
    adapter.subprocess_python = item.get("python")
    return adapter


def describe_all() -> list[dict]:
    described = []
    for name in names():
        try:
            info = get(name).describe()
        except Exception as error:  # an adapter that cannot even be imported is listed, disabled
            info = {"name": name, "display_name": name, "unavailable": f"{type(error).__name__}: {error}",
                    "options_schema": {"type": "object", "properties": {}}, "conditions": [],
                    "default_max_steps": 15, "has_architecture": False}
        info["subprocess_python"] = entry(name).get("python")
        described.append(info)
    return described


def reset() -> None:
    _entries.cache_clear()
