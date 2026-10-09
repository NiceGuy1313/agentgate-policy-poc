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


def load_task_spec(kind: str, suite: str, user_task_id: str, root: Path | None = None) -> TaskSpec:
    """kind = 'oracle' or 'llm:<model>'."""
    root = root or PROJECT_ROOT
    if kind == "oracle":
        path = root / "data" / "specs" / "oracle" / suite / f"{user_task_id}.yaml"
    elif kind.startswith("llm:"):
        model = kind.split(":", 1)[1].replace("/", "_")
        path = root / "data" / "specs" / "llm" / model / suite / f"{user_task_id}.yaml"
    else:
        raise ValueError(f"unknown spec kind {kind}")
    return TaskSpec.load(path)
