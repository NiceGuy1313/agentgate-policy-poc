"""Ollama HTTP client(`OllamaClient`): /api/chat, /api/tags, 일시 오류 재시도.

전제하는 endpoint 사실(Ollama docs: github.com/ollama/ollama docs/api.md, docs/cloud.mdx):
POST {host}/api/chat, GET {host}/api/tags. host는 cloud면 https://ollama.com(Authorization: Bearer $OLLAMA_API_KEY),
local server면 http://localhost:11434(키 없음)이다. cloud 모델 이름은 /api/tags가 반환하는 이름이다(ollama.com을
직접 호출할 때는 ":cloud" 접미사 없음). OLLAMA_NUM_CTX가 있으면 요청마다 options.num_ctx로 보낸다. request·response 메시지 형식은 agentgate/agents/ollama_agent.py에 있다.
"""

from __future__ import annotations

import os
import time

import requests

from agentgate.config import load_dotenv


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
        # local servers load the model with the server's context length; OLLAMA_NUM_CTX overrides it per request
        self.num_ctx = int(os.environ["OLLAMA_NUM_CTX"]) if os.environ.get("OLLAMA_NUM_CTX") else None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(self, payload: dict) -> dict:
        payload = {**payload, "stream": False}
        if self.num_ctx:
            payload["options"] = {**payload.get("options", {}), "num_ctx": self.num_ctx}
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
