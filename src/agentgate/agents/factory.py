"""평가 대상 에이전트의 사용량 집계(`UsageMeter`)와 TaskSpec provider를 제공한다.

에이전트 LLM은 `agentgate.adapters.native.make_native_llm`이 만든다(provider·모델은 `ModelSpec`). `UsageMeter`는 그
LLM의 token 사용량을 수집하고, `spec_provider`는 `Session`이 쓰는 TaskSpec 출처(oracle 또는 LLM 추출기)를 반환한다.
"""

from __future__ import annotations

import time

from agentgate.core.task_spec import TaskSpec
from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
from agentgate.scenarios.suites import describe_tools, load_suite
from agentgate.scenarios.task_specs import load_task_spec


class UsageMeter:
    """에이전트 LLM의 사용량 집계. ClaudeAgentLLM·OllamaAgentLLM은 스스로 세고, OpenAI client는 감싸서 센다."""

    def __init__(self):
        self.input_tokens = self.output_tokens = self.calls = 0
        self.ms = 0.0
        self.llm = None
        self.refusals = 0

    def collect(self):
        if self.llm is not None and hasattr(self.llm, "usage"):
            usage = self.llm.usage
            self.input_tokens, self.output_tokens, self.calls, self.ms = (usage["input_tokens"],
                                                                         usage["output_tokens"],
                                                                         usage["calls"], usage["ms"])
            self.refusals = usage.get("refusals", 0)

    def wrap_openai(self, client):
        original_create = client.chat.completions.create

        def create(*args, **kwargs):
            started = time.perf_counter()
            response = original_create(*args, **kwargs)
            self.ms += (time.perf_counter() - started) * 1000
            self.calls += 1
            usage = getattr(response, "usage", None)   # 일부 OpenAI 호환 서버는 usage를 주지 않는다
            self.input_tokens += (usage.prompt_tokens or 0) if usage else 0
            self.output_tokens += (usage.completion_tokens or 0) if usage else 0
            return response

        client.chat.completions.create = create
        return client


def spec_provider(kind: str, suite_name: str, user_task_id: str):
    """TaskSpec 출처: oracle(data/agentdojo/oracle_specs) 또는 llm(TaskSpecExtractor, prompts/extract_taskspec.md)."""
    def oracle(prompt):
        return (load_task_spec("oracle", suite_name, user_task_id),
                {"source": "oracle", "cached": True, "latency_ms": 0.0})

    if kind == "oracle":
        return oracle

    def llm(prompt):
        tools = [ToolInfo(**tool) for tool in describe_tools(load_suite(suite_name))]
        result = TaskSpecExtractor().extract(prompt, tools)
        meta = {"source": f"llm:{result.model}", "cached": result.cached,
                "latency_ms": 0.0 if result.cached else result.latency_ms,
                "input_tokens": 0 if result.cached else result.input_tokens,
                "output_tokens": 0 if result.cached else result.output_tokens,
                "error": result.error}
        # Extraction failure -> fail closed with an empty spec (nothing requested).
        return (result.spec or TaskSpec(notes="extraction failed")), meta

    return llm
