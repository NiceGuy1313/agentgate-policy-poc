"""live 평가용 평가 대상 에이전트 생성과 TaskSpec provider를 제공한다.

`make_llm`은 AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL(anthropic, ollama, openai)로 에이전트 LLM을 만들고,
`UsageMeter`는 token 사용량을 수집하며, `spec_provider`는 `build_pipeline`에 넘길 TaskSpec 출처를 반환한다.
"""

from __future__ import annotations

import os
import time

from agentgate.agents.ollama_agent import OllamaAgentLLM
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL
from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
from agentgate.suites import describe_tools, load_suite
from agentgate.task_spec import TaskSpec, load_task_spec


class UsageMeter:
    """Agent LLM usage. ClaudeAgentLLM and OllamaAgentLLM count their own usage; OpenAI clients are wrapped."""

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
            self.input_tokens += response.usage.prompt_tokens or 0
            self.output_tokens += response.usage.completion_tokens or 0
            return response

        client.chat.completions.create = create
        return client


def make_llm(meter: UsageMeter):
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    model = os.environ.get("AGENTGATE_AGENT_MODEL") or (DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else "")
    if not model:
        raise RuntimeError("AGENTGATE_AGENT_MODEL is not set")
    if provider == "anthropic":
        import anthropic

        from agentgate.agents.claude_agent import ClaudeAgentLLM
        llm = ClaudeAgentLLM(anthropic.Anthropic(), model)
        meter.llm = llm
    elif provider == "ollama":
        llm = OllamaAgentLLM(model)
        meter.llm = llm
    elif provider == "openai":
        import openai
        from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
        llm = OpenAILLM(meter.wrap_openai(openai.OpenAI()), model, temperature=0.0)
        llm.name = model
    else:
        raise ValueError(provider)
    return llm


def spec_provider(kind: str, suite_name: str, user_task_id: str):
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
