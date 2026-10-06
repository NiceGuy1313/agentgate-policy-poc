"""Policy primitives. No LLM or network calls are allowed anywhere in this package."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from agentgate.session import SessionState
from agentgate.spec import TaskSpec
from agentgate.trace import ValueIndex


class Verdict(str, enum.Enum):
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


RANK = {Verdict.ALLOW: 0, Verdict.ESCALATE: 1, Verdict.BLOCK: 2}


@dataclass
class Call:
    tool: str
    args: dict


@dataclass
class Decision:
    verdict: Verdict
    policy_id: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict.value, "policy_id": self.policy_id, "reason": self.reason,
                "evidence": self.evidence}


@dataclass
class Context:
    """Everything a policy may look at. `index` is the trace's ValueIndex."""

    spec: TaskSpec
    index: ValueIndex
    session: SessionState
    config: dict


class Policy:
    policy_id: str = "P?"

    def __init__(self, config: dict):
        self.config = config
        self.pc = config.get(self.policy_id, {}) or {}

    def applies_to(self, tool: str) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def evaluate(self, call: Call, ctx: Context) -> Decision | None:  # pragma: no cover
        raise NotImplementedError

    # helpers
    def block(self, reason: str, **ev) -> Decision:
        return Decision(Verdict.BLOCK, self.policy_id, reason, ev)

    def escalate(self, reason: str, **ev) -> Decision:
        return Decision(Verdict.ESCALATE, self.policy_id, reason, ev)

    def allow(self, reason: str, **ev) -> Decision:
        return Decision(Verdict.ALLOW, self.policy_id, reason, ev)
