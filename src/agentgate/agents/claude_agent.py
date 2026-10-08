"""Agent-under-test LLM element for current Claude models (subclass of AgentDojo's AnthropicLLM).

AgentDojo 0.1.35's `AnthropicLLM` targets older models: it always sends `temperature`, uses
`max_tokens=1024`, and rebuilds assistant turns as [thinking/text..., tool_use...]. On
`claude-sonnet-5-5` (the default here) and `claude-opus-5-5` sampling parameters are rejected, thinking is on by
default, and the model may emit progress-update thinking blocks *between* tool calls; editing an assistant turn's
blocks invalidates its thinking (preserved thinking). This element therefore
  - never sends `temperature` to models that reject it, sets `output_config.effort` instead,
  - uses a 16k output budget with streaming,
  - keeps every assistant turn's raw content blocks and replays them unchanged,
  - records token usage, latency and refusals (no model fallback: the agent under test must stay fixed).
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence

from agentdojo.agent_pipeline.llms.anthropic_llm import (
    AnthropicLLM,
    _conversation_to_anthropic,
    _function_to_anthropic,
    _parse_tool_calls_content,
)
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, text_content_block_from_string

from agentgate.llm.client import supports_temperature

RAW_CONTENT_KEY = "_anthropic_content"


class ClaudeAgentLLM(AnthropicLLM):
    def __init__(self, client, model: str, effort: str | None = None, max_tokens: int = 16000):
        # do not call AnthropicLLM.__init__: it converts to AsyncAnthropic and fixes temperature/max_tokens
        self.sync_client = client
        self.model = model
        self.name = model
        self.effort = effort or os.environ.get("AGENTGATE_AGENT_EFFORT") or "medium"
        self._MAX_TOKENS = max_tokens
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "ms": 0.0, "refusals": 0,
                      "max_tokens_stops": 0}

    def _request_kwargs(self) -> dict:
        if supports_temperature(self.model):
            return {"temperature": 0.0}
        return {"output_config": {"effort": self.effort}}

    def build_messages(self, messages: Sequence[ChatMessage]):
        system_prompt, converted = _conversation_to_anthropic(messages)
        raw_turns = [m.get(RAW_CONTENT_KEY) for m in messages if m["role"] == "assistant"]
        anthropic_messages, i = [], 0
        for message in converted:
            if message["role"] == "assistant":
                raw = raw_turns[i] if i < len(raw_turns) else None
                i += 1
                if raw is not None:
                    message = {"role": "assistant", "content": raw}
            anthropic_messages.append(message)
        return system_prompt, anthropic_messages

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {}):
        system_prompt, anthropic_messages = self.build_messages(messages)
        tools = [_function_to_anthropic(t) for t in runtime.functions.values()]
        request_kwargs = dict(model=self.model, max_tokens=self._MAX_TOKENS, messages=anthropic_messages,
                              **self._request_kwargs())
        if tools:
            request_kwargs["tools"] = tools
        if system_prompt:
            request_kwargs["system"] = system_prompt
        start = time.perf_counter()
        with self.sync_client.messages.stream(**request_kwargs) as stream:
            completion = stream.get_final_message()
        self.usage["ms"] += (time.perf_counter() - start) * 1000
        self.usage["calls"] += 1
        self.usage["input_tokens"] += completion.usage.input_tokens
        self.usage["output_tokens"] += completion.usage.output_tokens
        self.usage["refusals"] += completion.stop_reason == "refusal"
        self.usage["max_tokens_stops"] += completion.stop_reason == "max_tokens"

        tool_calls = _parse_tool_calls_content(completion) if completion.stop_reason == "tool_use" else None
        text = "".join(b.text for b in completion.content if b.type == "text")
        output = ChatAssistantMessage(role="assistant",
                                      content=[text_content_block_from_string(text)] if text else [],
                                      tool_calls=tool_calls or None)
        raw = [b.model_dump(mode="json", exclude_none=True) for b in completion.content]
        if tool_calls is None:   # e.g. stop_reason max_tokens mid tool call: never replay an unanswered tool_use
            raw = [b for b in raw if b.get("type") != "tool_use"]
        output[RAW_CONTENT_KEY] = raw  # type: ignore[literal-required]
        return query, runtime, env, [*messages, output], extra_args
