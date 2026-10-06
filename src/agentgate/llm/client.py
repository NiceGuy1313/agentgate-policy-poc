"""Provider-agnostic single-turn completion with latency and token accounting.

Anthropic defaults follow current model rules: the default model is `claude-sonnet-5-5`; on that
model (and every current Claude model except Haiku 4.5 / 4.6-generation and older) sampling
parameters such as `temperature` are rejected with a 400 and thinking is always on, so this client
never sends `temperature` to them and controls depth with `output_config.effort` instead.
Determinism therefore comes from the on-disk caches, not from temperature 0.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from agentgate.env import load_dotenv, require

KEY_OF = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "ollama": None}
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
# models that still accept `temperature` (everything newer rejects sampling parameters)
_TEMPERATURE_OK = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-sonnet-4-5", "claude-opus-4-5",
                   "claude-3")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def supports_temperature(model: str) -> bool:
    return model.startswith(_TEMPERATURE_OK)


@dataclass
class Completion:
    text: str
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None
    served_model: str | None = None


class LLMClient:
    """`AGENTGATE_LLM_PROVIDER` / `AGENTGATE_LLM_MODEL` / `AGENTGATE_LLM_EFFORT` / `AGENTGATE_LLM_FALLBACKS`."""

    def __init__(self, provider: str | None = None, model: str | None = None, temperature: float = 0.0,
                 effort: str | None = None):
        load_dotenv()
        self.provider = provider or os.environ.get("AGENTGATE_LLM_PROVIDER") or "anthropic"
        default = DEFAULT_ANTHROPIC_MODEL if self.provider == "anthropic" else ""
        self.model = model or os.environ.get("AGENTGATE_LLM_MODEL") or default
        if not self.model:
            raise RuntimeError("AGENTGATE_LLM_MODEL is not set (see .env.example)")
        if self.provider not in KEY_OF:
            raise ValueError(f"unsupported provider {self.provider}")
        if KEY_OF[self.provider]:
            require(KEY_OF[self.provider])
        self.temperature = temperature
        self.effort = effort or os.environ.get("AGENTGATE_LLM_EFFORT") or "medium"
        # server-side refusal fallback ("default" routing by refusal category); "off" disables it
        self.fallbacks = (os.environ.get("AGENTGATE_LLM_FALLBACKS") or "default").lower() != "off"
        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()
        elif self.provider == "ollama":
            from agentgate.ollama_llm import OllamaClient
            self._client = OllamaClient()
        else:
            import openai
            self._client = openai.OpenAI()

    def complete(self, system: str, user: str, max_tokens: int = 16000) -> Completion:
        t0 = time.perf_counter()
        if self.provider == "anthropic":
            kw = dict(model=self.model, system=system, max_tokens=max_tokens,
                      messages=[{"role": "user", "content": user}])
            if supports_temperature(self.model):
                kw["temperature"] = self.temperature
            else:
                kw["output_config"] = {"effort": self.effort}
            if self.fallbacks and not supports_temperature(self.model):
                r = self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
            else:
                r = self._client.messages.create(**kw)
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            it, ot = r.usage.input_tokens, r.usage.output_tokens
            stop, served = r.stop_reason, getattr(r, "model", self.model)
        elif self.provider == "ollama":
            d = self._client.chat({"model": self.model, "options": {"temperature": self.temperature, "seed": 0},
                                   "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user}]})
            text = (d.get("message") or {}).get("content") or ""
            it, ot = d.get("prompt_eval_count") or 0, d.get("eval_count") or 0
            stop, served = d.get("done_reason"), d.get("model", self.model)
        else:
            r = self._client.chat.completions.create(model=self.model, temperature=self.temperature,
                                                     max_tokens=max_tokens,
                                                     messages=[{"role": "system", "content": system},
                                                               {"role": "user", "content": user}])
            text = r.choices[0].message.content or ""
            it, ot = r.usage.prompt_tokens, r.usage.completion_tokens
            stop, served = r.choices[0].finish_reason, self.model
        return Completion(text, self.model, (time.perf_counter() - t0) * 1000, it, ot, stop, served)


def extract_json(text: str) -> str:
    """First top-level JSON object in a model reply (tolerates ```json fences)."""
    s = text.find("{")
    if s < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(s, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[s : i + 1]
    raise ValueError("unterminated JSON object")
