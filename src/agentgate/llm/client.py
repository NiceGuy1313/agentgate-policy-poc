"""Provider-agnostic single-turn completion with latency and token accounting."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from agentgate.env import load_dotenv, require

KEY_OF = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass
class Completion:
    text: str
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None, temperature: float = 0.0):
        load_dotenv()
        self.provider = provider or os.environ.get("AGENTGATE_LLM_PROVIDER", "anthropic")
        self.model = model or os.environ.get("AGENTGATE_LLM_MODEL", "")
        if not self.model:
            raise RuntimeError("AGENTGATE_LLM_MODEL is not set (see .env.example)")
        if self.provider not in KEY_OF:
            raise ValueError(f"unsupported provider {self.provider}")
        require(KEY_OF[self.provider])
        self.temperature = temperature
        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()
        else:
            import openai
            self._client = openai.OpenAI()

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> Completion:
        t0 = time.perf_counter()
        if self.provider == "anthropic":
            r = self._client.messages.create(model=self.model, system=system, max_tokens=max_tokens,
                                             temperature=self.temperature,
                                             messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            it, ot = r.usage.input_tokens, r.usage.output_tokens
        else:
            r = self._client.chat.completions.create(model=self.model, temperature=self.temperature,
                                                     max_tokens=max_tokens,
                                                     messages=[{"role": "system", "content": system},
                                                               {"role": "user", "content": user}])
            text = r.choices[0].message.content or ""
            it, ot = r.usage.prompt_tokens, r.usage.completion_tokens
        return Completion(text, self.model, (time.perf_counter() - t0) * 1000, it, ot)


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
