"""provider 독립 단일 턴 completion(`LLMClient`), Ollama HTTP client, JSON 응답 parsing을 제공한다.

Anthropic 기본값은 현행 모델 규칙을 따른다. 기본 모델은 `claude-sonnet-5-5`이다. 이 모델(및 Haiku 4.5 / 4.6 세대와
그 이전을 제외한 모든 현행 Claude 모델)은 `temperature` 등 sampling 파라미터를 400으로 거부하고 thinking이 항상
켜져 있으므로, 이 client는 `temperature`를 보내지 않고 `output_config.effort`로 깊이를 조절한다.
따라서 결정론은 temperature 0이 아니라 디스크 캐시에서 나온다.

`OllamaClient`가 전제하는 endpoint 사실(Ollama docs: github.com/ollama/ollama docs/api.md, docs/cloud.mdx):
POST {host}/api/chat, GET {host}/api/tags. host는 cloud면 https://ollama.com(Authorization: Bearer $OLLAMA_API_KEY),
local server면 http://localhost:11434(키 없음)이다. cloud 모델 이름은 /api/tags가 반환하는 이름이다(ollama.com을
직접 호출할 때는 ":cloud" 접미사 없음). request·response 메시지 형식은 agentgate/agents/ollama_agent.py에 있다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from agentgate.config import load_dotenv, require_env_vars

API_KEY_ENV_BY_PROVIDER = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "ollama": None}
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
# models that still accept `temperature` (everything newer rejects sampling parameters)
_MODELS_ACCEPTING_TEMPERATURE = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-sonnet-4-5",
                                 "claude-opus-4-5", "claude-3")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def supports_temperature(model: str) -> bool:
    return model.startswith(_MODELS_ACCEPTING_TEMPERATURE)


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
        default_model = DEFAULT_ANTHROPIC_MODEL if self.provider == "anthropic" else ""
        self.model = model or os.environ.get("AGENTGATE_LLM_MODEL") or default_model
        if not self.model:
            raise RuntimeError("AGENTGATE_LLM_MODEL is not set (see .env.example)")
        if self.provider not in API_KEY_ENV_BY_PROVIDER:
            raise ValueError(f"unsupported provider {self.provider}")
        if API_KEY_ENV_BY_PROVIDER[self.provider]:
            require_env_vars(API_KEY_ENV_BY_PROVIDER[self.provider])
        self.temperature = temperature
        self.effort = effort or os.environ.get("AGENTGATE_LLM_EFFORT") or "medium"
        # server-side refusal fallback ("default" routing by refusal category); "off" disables it
        self.fallbacks = (os.environ.get("AGENTGATE_LLM_FALLBACKS") or "default").lower() != "off"
        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()
        elif self.provider == "ollama":
            self._client = OllamaClient()
        else:
            import openai
            self._client = openai.OpenAI()

    def complete(self, system: str, user: str, max_tokens: int = 16000) -> Completion:
        start = time.perf_counter()
        if self.provider == "anthropic":
            request_kwargs = dict(model=self.model, system=system, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": user}])
            if supports_temperature(self.model):
                request_kwargs["temperature"] = self.temperature
            else:
                request_kwargs["output_config"] = {"effort": self.effort}
            if self.fallbacks and not supports_temperature(self.model):
                response = self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default",
                                                             **request_kwargs)
            else:
                response = self._client.messages.create(**request_kwargs)
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            input_tokens, output_tokens = response.usage.input_tokens, response.usage.output_tokens
            stop_reason, served_model = response.stop_reason, getattr(response, "model", self.model)
        elif self.provider == "ollama":
            data = self._client.chat({"model": self.model, "options": {"temperature": self.temperature, "seed": 0},
                                      "messages": [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}]})
            text = (data.get("message") or {}).get("content") or ""
            input_tokens, output_tokens = data.get("prompt_eval_count") or 0, data.get("eval_count") or 0
            stop_reason, served_model = data.get("done_reason"), data.get("model", self.model)
        else:
            response = self._client.chat.completions.create(model=self.model, temperature=self.temperature,
                                                            max_tokens=max_tokens,
                                                            messages=[{"role": "system", "content": system},
                                                                      {"role": "user", "content": user}])
            text = response.choices[0].message.content or ""
            input_tokens, output_tokens = response.usage.prompt_tokens, response.usage.completion_tokens
            stop_reason, served_model = response.choices[0].finish_reason, self.model
        return Completion(text, self.model, (time.perf_counter() - start) * 1000, input_tokens, output_tokens,
                          stop_reason, served_model)


class OllamaClient:
    """Minimal non-streaming client for Ollama's /api/chat and /api/tags with retry on transient failures."""

    def __init__(self, host: str | None = None, api_key: str | None = None, timeout: float = 600.0,
                 max_retries: int = 5):
        load_dotenv()
        self.host = (host or os.environ.get("OLLAMA_HOST") or "https://ollama.com").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY")
        if "ollama.com" in self.host and not self.api_key:
            raise RuntimeError("OLLAMA_API_KEY is not set (required for https://ollama.com)")
        self.timeout, self.max_retries = timeout, max_retries

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(self, payload: dict) -> dict:
        payload = {**payload, "stream": False}
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(f"{self.host}/api/chat", headers=self._headers(), json=payload,
                                         timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as error:   # incl. proxy disconnects
                last_error = f"{type(error).__name__}: {error}"[:500]
                if attempt == self.max_retries:
                    break
                time.sleep(min(2 ** attempt * 2, 30))
                continue
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == self.max_retries:
                break
            time.sleep(min(2 ** attempt * 2, 30))
        raise RuntimeError(f"Ollama /api/chat failed: {last_error}")

    def models(self) -> list[str]:
        response = requests.get(f"{self.host}/api/tags", headers=self._headers(), timeout=60)
        response.raise_for_status()
        return [m.get("name") or m.get("model") for m in response.json().get("models", [])]


def extract_first_json_object(text: str) -> str:
    """First top-level JSON object in a model reply (tolerates ```json fences)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unterminated JSON object")
