"""Provider-neutral model choice (`ModelSpec`) and its conversion to each framework's model arguments.

Providers
  anthropic      Anthropic API (ANTHROPIC_API_KEY)
  openai         OpenAI API (OPENAI_API_KEY)
  ollama         Ollama, cloud or local, through its OpenAI-compatible endpoint {OLLAMA_HOST}/v1
                 (OLLAMA_API_KEY for https://ollama.com; a local server needs no key)
  openai_compat  any OpenAI-compatible server (vLLM, LM Studio, the conformance fake LLM, ...): base_url required

A new provider is one entry in PROVIDERS; adapters only call `litellm_kwargs()` / `openai_kwargs()`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from agentgate.config import load_dotenv


@dataclass(frozen=True)
class Provider:
    name: str
    api_key_env: str | None
    default_base_url: str | None = None   # OpenAI-compatible base URL (None = vendor SDK default)
    base_url_env: str | None = None
    litellm_prefix: str = "openai"


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "ANTHROPIC_API_KEY", litellm_prefix="anthropic"),
    "openai": Provider("openai", "OPENAI_API_KEY"),
    "ollama": Provider("ollama", "OLLAMA_API_KEY", "https://ollama.com", "OLLAMA_HOST"),
    "openai_compat": Provider("openai_compat", None),
}


@dataclass
class ModelSpec:
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float | None = 0.0
    options: dict = field(default_factory=dict)   # passed through, e.g. {"reasoning_effort": "low"}

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"unknown provider {self.provider!r} (known: {', '.join(PROVIDERS)})")
        if not self.model:
            raise ValueError("model is required")

    @property
    def spec(self) -> Provider:
        return PROVIDERS[self.provider]

    def as_dict(self) -> dict:
        return asdict(self)

    def resolved_base_url(self) -> str | None:
        """OpenAI-compatible base URL including /v1, or None for the vendor default."""
        load_dotenv()
        provider = self.spec
        base = self.base_url or (os.environ.get(provider.base_url_env) if provider.base_url_env else None) \
            or provider.default_base_url
        if base is None:
            if self.provider == "openai_compat":
                raise ValueError("openai_compat needs base_url")
            return None
        base = base.rstrip("/")
        return base if self.provider == "openai_compat" or base.endswith("/v1") else base + "/v1"

    def api_key(self) -> str | None:
        load_dotenv()
        env = self.api_key_env or self.spec.api_key_env
        key = os.environ.get(env) if env else None
        # local OpenAI-compatible servers (ollama on localhost, vLLM, fakes) ignore the key but SDKs require one
        return key or ("not-needed" if self.provider in ("ollama", "openai_compat") else None)

    def missing_key(self) -> str | None:
        """Name of the required but unset key variable, or None."""
        load_dotenv()
        env = self.api_key_env or self.spec.api_key_env
        if env is None or os.environ.get(env):
            return None
        if self.provider == "ollama" and not (self.resolved_base_url() or "").startswith("https://ollama.com"):
            return None   # local ollama server
        return env

    def litellm_kwargs(self) -> dict:
        """Arguments for LiteLLM-based frameworks (smolagents LiteLLMModel, mini-swe-agent)."""
        kwargs = {"model_id": f"{self.spec.litellm_prefix}/{self.model}", "api_key": self.api_key()}
        base_url = self.resolved_base_url()
        if base_url:
            kwargs["api_base"] = base_url
        if self.temperature is not None and self.provider != "anthropic":
            kwargs["temperature"] = self.temperature
        kwargs.update(self.options)
        return kwargs

    def openai_kwargs(self) -> dict:
        """Arguments for OpenAI-SDK-style chat classes (browser-use ChatOpenAI, openai.OpenAI)."""
        return {"model": self.model, "base_url": self.resolved_base_url(), "api_key": self.api_key()}

    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def model_from_dict(data: dict) -> ModelSpec:
    known = {k: v for k, v in data.items() if k in ModelSpec.__dataclass_fields__}
    return ModelSpec(**known)
