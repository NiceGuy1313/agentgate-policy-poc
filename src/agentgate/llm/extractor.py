"""LLM TaskSpec extractor (Stage 4).

The signature is deliberately (prompt, tools): tool responses, ground truth and injection goals
cannot be passed in.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agentgate.llm.client import LLMClient, extract_json
from agentgate.spec import TaskSpec

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "prompts" / "extract_taskspec.md"
CACHE = ROOT / "data" / "specs" / "llm_cache"
SYSTEM = "You are a careful requirements analyst. You output only JSON."


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


def render(prompt: str, tools: list[ToolInfo]) -> str:
    tool_text = "\n".join(f"- {t.name}: {' '.join(t.description.split())[:300]}" for t in tools)
    return TEMPLATE.read_text().format(tools=tool_text, prompt=prompt)


def cache_key(prompt: str, tools: list[ToolInfo], model: str) -> str:
    h = hashlib.sha256()
    h.update(TEMPLATE.read_text().encode())
    h.update(prompt.encode())
    h.update(json.dumps([t.model_dump() for t in tools], sort_keys=True).encode())
    h.update(model.encode())
    return h.hexdigest()[:24]


class TaskSpecExtractor:
    """`model` defaults to AGENTGATE_LLM_MODEL; the API client is only created on a cache miss, so cached
    extractions can be reused without a key."""

    def __init__(self, client: LLMClient | None = None, cache_dir: Path = CACHE, model: str | None = None):
        import os

        from agentgate.env import load_dotenv
        from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL
        load_dotenv()
        self._client = client
        self.model = client.model if client else (model or os.environ.get("AGENTGATE_LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL)
        self.cache_dir = cache_dir / self.model.replace("/", "_").replace(":", "_")

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(model=self.model)
        return self._client

    def extract(self, prompt: str, tools: list[ToolInfo]) -> ExtractionResult:
        key = cache_key(prompt, tools, self.model)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            d = json.loads(path.read_text())
            spec = TaskSpec.model_validate(d["spec"]) if d.get("spec") else None
            return ExtractionResult(spec, d["model"], True, d["latency_ms"], d["input_tokens"], d["output_tokens"],
                                    d["attempts"], d.get("error"), d.get("raw", []))
        user = render(prompt, tools)
        res = ExtractionResult(None, self.model, False)
        for attempt in (1, 2):   # validation failure -> one retry, then record the error
            c = self.client.complete(SYSTEM, user if attempt == 1 else
                                     user + "\n\nYour previous reply was not valid. Reply with the JSON object only.")
            res.attempts = attempt
            res.latency_ms += c.latency_ms
            res.input_tokens += c.input_tokens
            res.output_tokens += c.output_tokens
            res.raw.append(c.text)
            try:
                res.spec = TaskSpec.model_validate(json.loads(extract_json(c.text)))
                res.error = None
                break
            except (ValueError, ValidationError) as e:
                res.error = f"{type(e).__name__}: {e}"[:500]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        d = asdict(res)
        d["spec"] = res.spec.model_dump() if res.spec else None
        path.write_text(json.dumps(d, indent=1, ensure_ascii=False))
        return res
