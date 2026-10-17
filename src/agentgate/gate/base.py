"""정책 primitive이다. 이 패키지 어디에서도 LLM 호출이나 네트워크 호출을 허용하지 않는다."""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agentgate.gate.normalization import normalize_value, parse_amount
from agentgate.gate.provenance import ValueIndex
from agentgate.gate.task_spec import TaskSpec


@dataclass
class SessionState:
    """Per-task state used by the stateful policies (P4, P5)."""

    sent_email_ids: list[str] = field(default_factory=list)
    transfers: list[tuple[str, float]] = field(default_factory=list)
    cumulative_amount: float = 0.0
    transfers_per_recipient: Counter = field(default_factory=Counter)
    read_registry: list[dict] = field(default_factory=list)   # {sid, tool, sensitive}

    def record_call(self, tool: str, args: dict, raw_output, sensitive: list[str], source_id: str) -> None:
        if tool == "send_email" and raw_output is not None:
            email_id = getattr(raw_output, "id_", None)
            if email_id is not None:
                self.sent_email_ids.append(str(email_id))
        if tool in ("send_money", "schedule_transaction"):
            amount = parse_amount(args.get("amount")) or 0.0
            recipient = normalize_value(args.get("recipient", ""))
            self.transfers.append((recipient, amount))
            self.cumulative_amount += amount
            self.transfers_per_recipient[recipient] += 1
        if sensitive:
            self.read_registry.append({"sid": source_id, "tool": tool, "sensitive": sensitive})


class Verdict(str, enum.Enum):
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


VERDICT_SEVERITY = {Verdict.ALLOW: 0, Verdict.ESCALATE: 1, Verdict.BLOCK: 2}


def strictest_verdict(verdicts: Iterable[Verdict]) -> Verdict:
    """BLOCK > ESCALATE > ALLOW; ALLOW for an empty input."""
    strictest = Verdict.ALLOW
    for verdict in verdicts:
        if VERDICT_SEVERITY[verdict] > VERDICT_SEVERITY[strictest]:
            strictest = verdict
    return strictest


@dataclass
class ToolCall:
    tool: str
    args: dict


@dataclass
class PolicyDecision:
    verdict: Verdict
    policy_id: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict.value, "policy_id": self.policy_id, "reason": self.reason,
                "evidence": self.evidence}


@dataclass
class PolicyContext:
    """Everything a policy may look at: the task spec, the trace's ValueIndex and the session state."""

    spec: TaskSpec
    value_index: ValueIndex
    session: SessionState
    config: dict


class Policy:
    policy_id: str = "P?"

    def __init__(self, config: dict):
        self.config = config
        self.policy_config = config.get(self.policy_id, {}) or {}

    def applies_to(self, tool: str) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def evaluate(self, call: ToolCall, context: PolicyContext) -> PolicyDecision | None:  # pragma: no cover
        raise NotImplementedError

    def block(self, reason: str, **evidence) -> PolicyDecision:
        return PolicyDecision(Verdict.BLOCK, self.policy_id, reason, evidence)

    def escalate(self, reason: str, **evidence) -> PolicyDecision:
        return PolicyDecision(Verdict.ESCALATE, self.policy_id, reason, evidence)

    def allow(self, reason: str, **evidence) -> PolicyDecision:
        return PolicyDecision(Verdict.ALLOW, self.policy_id, reason, evidence)
