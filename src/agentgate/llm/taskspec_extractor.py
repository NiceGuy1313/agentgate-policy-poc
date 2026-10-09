"""LLM TaskSpec 추출기이다.

signature를 의도적으로 (prompt, tools)로 둔다. 툴 응답, ground truth, injection goal은 넘길 수 없다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agentgate.config import PROJECT_ROOT, load_dotenv
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL, LLMClient, extract_first_json_object
from agentgate.task_spec import TaskSpec

PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "prompts" / "extract_taskspec.md"
CACHE_DIR = PROJECT_ROOT / "data" / "specs" / "llm_cache"
SYSTEM_PROMPT = "You are a careful requirements analyst. You output only JSON."


class ToolInfo(BaseModel):
    name: str
    description: str


@dataclass
class ExtractionResult:
    spec: TaskSpec | None
    model: str
    cached: bool
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    error: str | None = None
    raw: list[str] = field(default_factory=list)


def render_extraction_prompt(prompt: str, tools: list[ToolInfo]) -> str:
    tool_text = "\n".join(f"- {t.name}: {' '.join(t.description.split())[:300]}" for t in tools)
    return PROMPT_TEMPLATE_PATH.read_text().format(tools=tool_text, prompt=prompt)


def compute_cache_key(prompt: str, tools: list[ToolInfo], model: str) -> str:
    digest = hashlib.sha256()
    digest.update(PROMPT_TEMPLATE_PATH.read_text().encode())
    digest.update(prompt.encode())
    digest.update(json.dumps([t.model_dump() for t in tools], sort_keys=True).encode())
    digest.update(model.encode())
    return digest.hexdigest()[:24]


class TaskSpecExtractor:
    """`model` defaults to AGENTGATE_LLM_MODEL; the API client is only created on a cache miss, so cached
    extractions can be reused without a key."""

    def __init__(self, client: LLMClient | None = None, cache_dir: Path = CACHE_DIR, model: str | None = None):
        load_dotenv()
        self._client = client
        self.model = client.model if client else (model or os.environ.get("AGENTGATE_LLM_MODEL")
                                                  or DEFAULT_ANTHROPIC_MODEL)
        self.cache_dir = cache_dir / self.model.replace("/", "_").replace(":", "_")

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(model=self.model)
        return self._client

    def extract(self, prompt: str, tools: list[ToolInfo]) -> ExtractionResult:
        key = compute_cache_key(prompt, tools, self.model)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            spec = TaskSpec.model_validate(cached["spec"]) if cached.get("spec") else None
            return ExtractionResult(spec, cached["model"], True, cached["latency_ms"], cached["input_tokens"],
                                    cached["output_tokens"], cached["attempts"], cached.get("error"),
                                    cached.get("raw", []))
        user_prompt = render_extraction_prompt(prompt, tools)
        result = ExtractionResult(None, self.model, False)
        for attempt in (1, 2):   # validation failure -> one retry, then record the error
            completion = self.client.complete(SYSTEM_PROMPT, user_prompt if attempt == 1 else
                                              user_prompt + "\n\nYour previous reply was not valid. "
                                              "Reply with the JSON object only.")
            result.attempts = attempt
            result.latency_ms += completion.latency_ms
            result.input_tokens += completion.input_tokens
            result.output_tokens += completion.output_tokens
            result.raw.append(completion.text)
            try:
                result.spec = TaskSpec.model_validate(json.loads(extract_first_json_object(completion.text)))
                result.error = None
                break
            except (ValueError, ValidationError) as error:
                result.error = f"{type(error).__name__}: {error}"[:500]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = asdict(result)
        record["spec"] = result.spec.model_dump() if result.spec else None
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False))
        return result
