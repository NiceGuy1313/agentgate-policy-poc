"""LLM 구성 요소: API client와 TaskSpec 추출기. 하위 모듈은 처음 쓸 때 import한다.

판정 LLM은 `agentgate.policy_gate.llm_judge`(gate_llm_judge)에, ToolSpec 추출기는 `agentgate.policy_gate.toolspec`에
있다. 평가 대상 에이전트(`agentgate.agents`)도 `llm.client`를 쓴다.
Gate 공통부(`agentgate.core`)와 룰북 정적 검사기(`agentgate.policy_gate.rule_checker`)는 이 패키지를 import하지 않는다.
"""

from __future__ import annotations

import importlib

_EXPORTS = {"LLMClient": "client", "OllamaClient": "client", "ExtractionResult": "taskspec_extractor",
            "TaskSpecExtractor": "taskspec_extractor", "ToolInfo": "taskspec_extractor"}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(importlib.import_module(f"agentgate.llm.{_EXPORTS[name]}"), name)
    raise AttributeError(f"module 'agentgate.llm' has no attribute {name!r}")


__all__ = ["ExtractionResult", "LLMClient", "OllamaClient", "TaskSpecExtractor", "ToolInfo"]
