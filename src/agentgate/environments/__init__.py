"""평가 환경 등록부: 이름 -> `Environment` 구현. 기본은 AgentDojo(`agentgate.environments.agentdojo`)다.

구현은 처음 쓸 때 import한다(이 패키지를 import해도 AgentDojo suite를 읽지 않는다).
"""

from __future__ import annotations

import importlib

from agentgate.environments.base import Environment, ScenarioRun, ToolBinding

ENVIRONMENTS: dict[str, str] = {"agentdojo": "agentgate.environments.agentdojo:AgentDojoEnvironment"}
_INSTANCES: dict[str, Environment] = {}


def get_environment(name: str = "agentdojo") -> Environment:
    if name not in ENVIRONMENTS:
        raise ValueError(f"unknown environment {name!r} (known: {', '.join(ENVIRONMENTS)})")
    if name not in _INSTANCES:
        module, _, attr = ENVIRONMENTS[name].partition(":")
        _INSTANCES[name] = getattr(importlib.import_module(module), attr)()
    return _INSTANCES[name]


__all__ = ["ENVIRONMENTS", "Environment", "ScenarioRun", "ToolBinding", "get_environment"]
