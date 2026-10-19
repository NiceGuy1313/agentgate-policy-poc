"""Gate 공통 자료형: 판정(Verdict), 툴 호출, 판정 문맥, 세션 상태. 이 패키지에서는 LLM·네트워크 호출을 하지 않는다."""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from agentgate.core.normalization import normalize_value, parse_amount
from agentgate.core.provenance import ValueIndex
from agentgate.core.task_spec import TaskSpec


@dataclass(frozen=True)
class SessionBindings:
    """Which calls SessionState records, from the tool environment binding (config/agentdojo/tool_args.yaml roles and
    tool_roles.yaml categories), so the gate core names no tool."""

    sent_message_tools: frozenset[str]      # external_send tools of kind message: output id_ = a sent message
    transfer_tools: dict[str, tuple[str | None, str | None]]   # money tools making a new transfer -> (recipient, amount) arg

    @classmethod
    def from_config(cls) -> "SessionBindings":
        from agentgate.config import load_tool_args, load_tool_roles
        tool_args, categories = load_tool_args(), load_tool_roles()
        roles = tool_args.get("tools", {})
        messages = set(tool_args.get("message_tools", []))
        transfers = {}
        for tool in categories.get("money", []):
            tool_roles = roles.get(tool, {})
            if tool_roles.get("amount") and not tool_roles.get("target_id"):   # a new transfer, not an update
                transfers[tool] = ((tool_roles.get("destination") or [None])[0], tool_roles["amount"][0])
        return cls(frozenset(set(categories.get("external_send", [])) & messages), transfers)


@lru_cache(maxsize=1)
def default_session_bindings() -> SessionBindings:
    return SessionBindings.from_config()


@dataclass
class SessionState:
    """Per-task state: sent messages, transfers and sensitive reads seen so far."""

    sent_message_ids: list[str] = field(default_factory=list)
    transfers: list[tuple[str, float]] = field(default_factory=list)
    cumulative_amount: float = 0.0
    transfers_per_recipient: Counter = field(default_factory=Counter)
    read_registry: list[dict] = field(default_factory=list)   # {sid, tool, sensitive}
    bindings: SessionBindings | None = None                   # None: default_session_bindings()

    def record_call(self, tool: str, args: dict, raw_output, sensitive: list[str], source_id: str) -> None:
        bindings = self.bindings or default_session_bindings()
        if tool in bindings.sent_message_tools and raw_output is not None:
            message_id = getattr(raw_output, "id_", None)
            if message_id is not None:
                self.sent_message_ids.append(str(message_id))
        if tool in bindings.transfer_tools:
            recipient_arg, amount_arg = bindings.transfer_tools[tool]
            amount = parse_amount(args.get(amount_arg)) or 0.0
            recipient = normalize_value(args.get(recipient_arg, "") if recipient_arg else "")
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
    """Everything a judgement may look at: the task spec, the trace's ValueIndex, the session state, the gate config."""

    spec: TaskSpec
    value_index: ValueIndex
    session: SessionState
    config: dict

