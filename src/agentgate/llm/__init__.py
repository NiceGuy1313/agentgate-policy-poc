"""버전과 무관한 LLM 구성 요소: API client, TaskSpec 추출기, 판정 LLM 입출력 helper.

버전별 판정 LLM은 `agentgate.v1.judge`(C4), `agentgate.v3.judge`(C10a)에,
ToolSpec 추출기는 `agentgate.v3.toolspec`에 있다. 평가 대상 에이전트(`agentgate.agents`)도 `llm.client`를 쓴다.
정책 코드(`agentgate.gate`, `agentgate.v*/policies.py`)는 이 패키지를 import하지 않는다.
"""

from agentgate.llm.client import LLMClient, OllamaClient
from agentgate.llm.taskspec_extractor import ExtractionResult, TaskSpecExtractor, ToolInfo

__all__ = ["ExtractionResult", "LLMClient", "OllamaClient", "TaskSpecExtractor", "ToolInfo"]
