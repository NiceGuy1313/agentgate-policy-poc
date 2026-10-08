"""LLM components. Within the gate only the TaskSpec extractor and the escalation judge call an LLM.

`llm.client` is also used by the agents under test in `agentgate.agents` (the agent-under-test exception);
the policy package (`agentgate.policy`) never imports this package.
"""

from agentgate.llm.client import LLMClient, OllamaClient
from agentgate.llm.escalation_judge import EscalationJudge, as_runtime_judge
from agentgate.llm.taskspec_extractor import ExtractionResult, TaskSpecExtractor, ToolInfo

__all__ = ["EscalationJudge", "ExtractionResult", "LLMClient", "OllamaClient", "TaskSpecExtractor", "ToolInfo",
           "as_runtime_judge"]
