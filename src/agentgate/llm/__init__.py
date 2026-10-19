"""LLM 구성 요소: API client, TaskSpec 추출기, 판정 LLM 입출력 helper.

판정 LLM은 `agentgate.policy_gate.llm_judge`(gate_llm_judge)에, ToolSpec 추출기는 `agentgate.policy_gate.toolspec`에
있다. 평가 대상 에이전트(`agentgate.agents`)도 `llm.client`를 쓴다.
Gate 공통부(`agentgate.core`)와 룰북 정적 검사기(`agentgate.policy_gate.rule_checker`)는 이 패키지를 import하지 않는다.
"""

from agentgate.llm.client import LLMClient, OllamaClient
from agentgate.llm.taskspec_extractor import ExtractionResult, TaskSpecExtractor, ToolInfo

__all__ = ["ExtractionResult", "LLMClient", "OllamaClient", "TaskSpecExtractor", "ToolInfo"]
