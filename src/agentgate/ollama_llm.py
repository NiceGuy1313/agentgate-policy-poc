"""Ollama (cloud or local) chat client and AgentDojo agent element.

API facts this module relies on (Ollama docs: github.com/ollama/ollama docs/api.md, docs/cloud.mdx;
ollama-python ollama/_types.py):
- endpoint  POST {host}/api/chat   host = https://ollama.com for cloud (Authorization: Bearer $OLLAMA_API_KEY),
            http://localhost:11434 for a local server (no key). Cloud model names are the ones /api/tags returns
            (no ":cloud" suffix when calling ollama.com directly).
- request   {"model", "messages", "tools": [{"type": "function", "function": {name, description, parameters}}],
             "stream": false, "options": {"temperature", "seed"}, "think": bool|"low"|"medium"|"high"}
- response  {"message": {"role": "assistant", "content", "thinking"?, "tool_calls"?: [{"function": {"name",
             "arguments": <object>}}]}, "done": true, "done_reason", "prompt_eval_count", "eval_count"}
            Docs show no id; ollama.com responses observed on 2026-10-06 (glm-5.3) do carry "id" and
            function.index - the id is used when present, otherwise one is generated. arguments is a JSON object
            (some models emit a JSON string - handled).
- tool result message  {"role": "tool", "content": "...", "tool_name": "<function name>"}
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence

import requests
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, get_text_content_as_str, text_content_block_from_string

from agentgate.env import load_dotenv

RAW_KEY = "_ollama_message"
_VALID_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class OllamaClient:
    def __init__(self, host: str | None = None, api_key: str | None = None, timeout: float = 600.0,
                 max_retries: int = 5):
        load_dotenv()
        self.host = (host or os.environ.get("OLLAMA_HOST") or "https://ollama.com").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY")
        if "ollama.com" in self.host and not self.api_key:
            raise RuntimeError("OLLAMA_API_KEY is not set (required for https://ollama.com)")
        self.timeout, self.max_retries = timeout, max_retries

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, payload: dict) -> dict:
        payload = {**payload, "stream": False}
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(f"{self.host}/api/chat", headers=self._headers(), json=payload, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:   # incl. proxy disconnects
                last = f"{type(e).__name__}: {e}"[:500]
                if attempt == self.max_retries:
                    break
                time.sleep(min(2 ** attempt * 2, 30))
                continue
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:500]}"
            if r.status_code not in (408, 429, 500, 502, 503, 504) or attempt == self.max_retries:
                break
            time.sleep(min(2 ** attempt * 2, 30))
        raise RuntimeError(f"Ollama /api/chat failed: {last}")

    def models(self) -> list[str]:
        r = requests.get(f"{self.host}/api/tags", headers=self._headers(), timeout=60)
        r.raise_for_status()
        return [m.get("name") or m.get("model") for m in r.json().get("models", [])]


def parse_arguments(args) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            v = json.loads(args)
            return v if isinstance(v, dict) else {"value": v}
        except json.JSONDecodeError:
            return {"__raw__": args}
    return {}


def tool_schema(f) -> dict:
    return {"type": "function", "function": {"name": f.name, "description": f.description,
                                             "parameters": f.parameters.model_json_schema()}}


def to_ollama_messages(messages: Sequence[ChatMessage]) -> list[dict]:
    out = []
    for m in messages:
        role = m["role"]
        if role in ("system", "user"):
            out.append({"role": role, "content": get_text_content_as_str(m["content"] or [])})
        elif role == "assistant":
            raw = m.get(RAW_KEY)
            if raw is not None:            # replay exactly what the model returned
                out.append(raw)
                continue
            msg = {"role": "assistant", "content": get_text_content_as_str(m["content"] or [])}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"function": {"name": tc.function, "arguments": dict(tc.args)}}
                                     for tc in m["tool_calls"]]
            out.append(msg)
        elif role == "tool":
            text = m["error"] if m.get("error") else get_text_content_as_str(m["content"] or [])
            tc = m.get("tool_call")
            out.append({"role": "tool", "content": text, "tool_name": tc.function if tc else ""})
    return out


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
                   "tools": [tool_schema(f) for f in runtime.functions.values()], "options": self.options}
        if self.think is not None:
            payload["think"] = self.think
        t0 = time.perf_counter()
        data = self.client.chat(payload)
        self.usage["ms"] += (time.perf_counter() - t0) * 1000
        self.usage["calls"] += 1
        self.usage["input_tokens"] += data.get("prompt_eval_count") or 0
        self.usage["output_tokens"] += data.get("eval_count") or 0
        msg = data.get("message") or {}
        self._turn += 1
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            name = fn.get("name") or ""
            if not _VALID_NAME.match(name):
                self.usage["invalid_tool_calls"] += 1
                continue
            calls.append(FunctionCall(function=name, args=parse_arguments(fn.get("arguments")),
                                      id=(tc or {}).get("id") or f"ollama_{self._turn}_{i}"))
        text = msg.get("content") or ""
        out = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string(text)] if text else [],
                                   tool_calls=calls or None)
        raw = {k: v for k, v in msg.items() if k in ("role", "content", "thinking", "tool_calls")}
        raw["role"] = "assistant"
        if calls:   # replay only the calls that were executed (invalid names are dropped above)
            raw["tool_calls"] = [tc for tc in msg.get("tool_calls") or []
                                 if _VALID_NAME.match(((tc or {}).get("function") or {}).get("name") or "")]
        else:
            raw.pop("tool_calls", None)
        out[RAW_KEY] = raw  # type: ignore[literal-required]
        return query, runtime, env, [*messages, out], extra_args
