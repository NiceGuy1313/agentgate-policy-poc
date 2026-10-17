"""LLM TaskSpec 추출기이다.

signature를 의도적으로 (prompt, tools)로 둔다. 툴 응답, ground truth, injection goal은 넘길 수 없다.
추출 프롬프트와 캐시 위치는 버전이 정한다: `TaskSpecExtractor(version="v3")`는 prompts/v3/extract_taskspec.md를
쓰고 data/v3/cache/taskspec/<model>/에 저장한다(기본 v1).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agentgate.config import PROJECT_ROOT, load_dotenv, write_text_atomic
from agentgate.gate.task_spec import TaskSpec
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL, LLMClient, extract_first_json_object

VERSION_PROMPTS = {"v1": PROJECT_ROOT / "prompts" / "v1" / "extract_taskspec.md",
                   "v2": PROJECT_ROOT / "prompts" / "v1" / "extract_taskspec.md",   # v2 reuses the v1 prompt
                   "v3": PROJECT_ROOT / "prompts" / "v3" / "extract_taskspec.md"}
PROMPT_TEMPLATE_PATH = VERSION_PROMPTS["v1"]


def cache_dir_for(version: str) -> Path:
    return PROJECT_ROOT / "data" / version / "cache" / "taskspec"


CACHE_DIR = cache_dir_for("v1")
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


def render_extraction_prompt(prompt: str, tools: list[ToolInfo], template_path: Path = PROMPT_TEMPLATE_PATH) -> str:
    tool_text = "\n".join(f"- {t.name}: {' '.join(t.description.split())[:300]}" for t in tools)
    return template_path.read_text().format(tools=tool_text, prompt=prompt)


def compute_cache_key(prompt: str, tools: list[ToolInfo], model: str, template_path: Path = PROMPT_TEMPLATE_PATH) -> str:
    digest = hashlib.sha256()
    digest.update(template_path.read_text().encode())
    digest.update(prompt.encode())
    digest.update(json.dumps([t.model_dump() for t in tools], sort_keys=True).encode())
    digest.update(model.encode())
    return digest.hexdigest()[:24]


class TaskSpecExtractor:
    """`model` defaults to AGENTGATE_LLM_MODEL; the API client is only created on a cache miss, so cached
    extractions can be reused without a key."""

    def __init__(self, client: LLMClient | None = None, cache_dir: Path | None = None, model: str | None = None,
                 version: str = "v1"):
        load_dotenv()
        self.version = version
        self.template_path = VERSION_PROMPTS[version]
        self._client = client
        self.model = client.model if client else (model or os.environ.get("AGENTGATE_LLM_MODEL")
                                                  or DEFAULT_ANTHROPIC_MODEL)
        # AGENTGATE_TASKSPEC_CACHE_DIR: a fresh directory forces live extraction (e.g. a live run that must not reuse
        # earlier specs); the default reuses the committed cache.
        cache_dir = cache_dir or Path(os.environ.get("AGENTGATE_TASKSPEC_CACHE_DIR") or cache_dir_for(version))
        self.cache_dir = cache_dir / self.model.replace("/", "_").replace(":", "_")

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(model=self.model)
        return self._client

    def extract(self, prompt: str, tools: list[ToolInfo]) -> ExtractionResult:
        key = compute_cache_key(prompt, tools, self.model, self.template_path)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            spec = TaskSpec.model_validate(cached["spec"]) if cached.get("spec") else None
            return ExtractionResult(spec, cached["model"], True, cached["latency_ms"], cached["input_tokens"],
                                    cached["output_tokens"], cached["attempts"], cached.get("error"),
                                    cached.get("raw", []))
        user_prompt = render_extraction_prompt(prompt, tools, self.template_path)
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
        write_text_atomic(path, json.dumps(record, indent=1, ensure_ascii=False))
        return result
