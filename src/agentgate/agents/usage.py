"""평가 대상 에이전트 LLM의 사용량 집계(`UsageMeter`)."""

from __future__ import annotations

import time


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
