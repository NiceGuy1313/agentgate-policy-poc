"""LLM 구성 요소이다. 게이트 안에서 LLM을 호출하는 것은 TaskSpec 추출기와 판정 LLM(escalation judge)뿐이다.

평가 대상 에이전트(`agentgate.agents`)도 `llm.client`를 사용한다(평가 대상 에이전트 예외).
정책 패키지(`agentgate.policy`)는 이 패키지를 import하지 않는다.
"""

from agentgate.llm.client import LLMClient, OllamaClient
from agentgate.llm.escalation_judge import EscalationJudge, as_runtime_judge
from agentgate.llm.taskspec_extractor import ExtractionResult, TaskSpecExtractor, ToolInfo

__all__ = ["EscalationJudge", "ExtractionResult", "LLMClient", "OllamaClient", "TaskSpecExtractor", "ToolInfo",
           "as_runtime_judge"]
