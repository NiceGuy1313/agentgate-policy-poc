"""TaskSpec: 사용자가 실제로 요청한 범위를 표현한다(docs/policy_spec.md 3절)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agentgate.config import PROJECT_ROOT


class TaskSpec(BaseModel):
    requested_ops: list[str] = Field(default_factory=list)
    """State-changing / outbound tool names the prompt asks for (real AgentDojo tool names)."""
    named_sources: list[str] = Field(default_factory=list)
    """Data sources the user explicitly points to (file names, URLs, senders, channels, cities,
    or `tool:<name>` for a whole tool such as the transaction history)."""
    allowed_dest: list[str] = Field(default_factory=list)
    """Destinations (e-mail, IBAN, URL, user name) stated in the prompt."""
    amount_cap: float | None = None
    """Upper bound for any single money transfer stated or implied by the prompt."""
    egress_requested: bool = False
    """True if the user asks to send content derived from their data to someone."""
    delegated_sources: list[str] = Field(default_factory=list)
    """Sources whose instructions the user explicitly asks the assistant to carry out ("do what the e-mail from David says")."""
    notes: str | None = None
    status: str | None = None
    """draft | reviewed (oracle specs only)."""

    @classmethod
    def load(cls, path: str | Path) -> "TaskSpec":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()) or {})

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True)
        )


def llm_spec_dir(model: str, root: Path | None = None) -> Path:
    """Stored LLM TaskSpecs (scripts/taskspec/extract_llm_specs.py): data/cache/specs/<model>/."""
    return (root or PROJECT_ROOT) / "data" / "cache" / "specs" / model.replace("/", "_")
