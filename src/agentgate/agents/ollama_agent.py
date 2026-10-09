"""Ollama 모델(cloud 또는 local)용 평가 대상 AgentDojo element이다. HTTP 전송은 `llm.client.OllamaClient`가 맡는다.

이 모듈이 전제하는 메시지 형식(Ollama docs: github.com/ollama/ollama docs/api.md; ollama-python ollama/_types.py).
endpoint, host, 인증 관련 사실은 agentgate/llm/client.py에 있다.
- request   {"model", "messages", "tools": [{"type": "function", "function": {name, description, parameters}}],
             "stream": false, "options": {"temperature", "seed"}, "think": bool|"low"|"medium"|"high"}
- response  {"message": {"role": "assistant", "content", "thinking"?, "tool_calls"?: [{"function": {"name",
             "arguments": <object>}}]}, "done": true, "done_reason", "prompt_eval_count", "eval_count"}
            docs에는 id가 없으나 2026-10-06 ollama.com 응답(glm-5.3)에는 "id"와 function.index가 있었다. id가 있으면
            그대로 쓰고 없으면 생성한다. arguments는 JSON object이다(JSON string을 내는 모델도 처리한다).
- tool result message  {"role": "tool", "content": "...", "tool_name": "<function name>"}
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, get_text_content_as_str, text_content_block_from_string

from agentgate.llm.client import OllamaClient

RAW_MESSAGE_KEY = "_ollama_message"
_VALID_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def parse_tool_arguments(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            value = json.loads(arguments)
            return value if isinstance(value, dict) else {"value": value}
        except json.JSONDecodeError:
            return {"__raw__": arguments}
    return {}


def build_tool_schema(function) -> dict:
    return {"type": "function", "function": {"name": function.name, "description": function.description,
                                             "parameters": function.parameters.model_json_schema()}}


def to_ollama_messages(messages: Sequence[ChatMessage]) -> list[dict]:
    ollama_messages = []
    for message in messages:
        role = message["role"]
        if role in ("system", "user"):
            ollama_messages.append({"role": role, "content": get_text_content_as_str(message["content"] or [])})
        elif role == "assistant":
            raw = message.get(RAW_MESSAGE_KEY)
            if raw is not None:            # replay exactly what the model returned
                ollama_messages.append(raw)
                continue
            assistant_message = {"role": "assistant", "content": get_text_content_as_str(message["content"] or [])}
            if message.get("tool_calls"):
                assistant_message["tool_calls"] = [{"function": {"name": tc.function, "arguments": dict(tc.args)}}
                                                   for tc in message["tool_calls"]]
            ollama_messages.append(assistant_message)
        elif role == "tool":
            text = message["error"] if message.get("error") else get_text_content_as_str(message["content"] or [])
            tool_call = message.get("tool_call")
            ollama_messages.append({"role": "tool", "content": text,
                                    "tool_name": tool_call.function if tool_call else ""})
    return ollama_messages


class OllamaAgentLLM(BasePipelineElement):
    """Agent-under-test element for Ollama models (AgentDojo has no Ollama element)."""

    def __init__(self, model: str, client: OllamaClient | None = None, think=None, temperature: float = 0.0,
                 seed: int = 0):
        self.model = model
        self.name = model
        self.client = client or OllamaClient()
        env_think = os.environ.get("AGENTGATE_AGENT_THINK")
        self.think = think if think is not None else (None if not env_think else
                                                     {"true": True, "false": False}.get(env_think.lower(), env_think))
        self.options = {"temperature": temperature, "seed": seed}
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "ms": 0.0, "refusals": 0,
                      "invalid_tool_calls": 0}
        self._turn = 0

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {}):
        payload = {"model": self.model, "messages": to_ollama_messages(messages),
                   "tools": [build_tool_schema(f) for f in runtime.functions.values()], "options": self.options}
        if self.think is not None:
            payload["think"] = self.think
        start = time.perf_counter()
        data = self.client.chat(payload)
        self.usage["ms"] += (time.perf_counter() - start) * 1000
        self.usage["calls"] += 1
        self.usage["input_tokens"] += data.get("prompt_eval_count") or 0
        self.usage["output_tokens"] += data.get("eval_count") or 0
        response_message = data.get("message") or {}
        self._turn += 1
        calls = []
        for i, tool_call in enumerate(response_message.get("tool_calls") or []):
            function = (tool_call or {}).get("function") or {}
            name = function.get("name") or ""
            if not _VALID_TOOL_NAME_PATTERN.match(name):
                self.usage["invalid_tool_calls"] += 1
                continue
            calls.append(FunctionCall(function=name, args=parse_tool_arguments(function.get("arguments")),
                                      id=(tool_call or {}).get("id") or f"ollama_{self._turn}_{i}"))
        text = response_message.get("content") or ""
        output = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string(text)] if text else [],
                                      tool_calls=calls or None)
        raw = {k: v for k, v in response_message.items() if k in ("role", "content", "thinking", "tool_calls")}
        raw["role"] = "assistant"
        if calls:   # replay only the calls that were executed (invalid names are dropped above)
            raw["tool_calls"] = [
                tc for tc in response_message.get("tool_calls") or []
                if _VALID_TOOL_NAME_PATTERN.match(((tc or {}).get("function") or {}).get("name") or "")]
        else:
            raw.pop("tool_calls", None)
        output[RAW_MESSAGE_KEY] = raw  # type: ignore[literal-required]
        return query, runtime, env, [*messages, output], extra_args
