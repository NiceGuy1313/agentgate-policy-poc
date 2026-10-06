"""Per-task session state used by stateful policies (P4, P5)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from agentgate import normalize as N


@dataclass
class SessionState:
    sent_email_ids: list[str] = field(default_factory=list)
    transfers: list[tuple[str, float]] = field(default_factory=list)
    cumulative_amount: float = 0.0
    transfers_per_recipient: Counter = field(default_factory=Counter)
    read_registry: list[dict] = field(default_factory=list)   # {sid, tool, sensitive}

    def record(self, tool: str, args: dict, raw_output, sensitive: list[str], sid: str) -> None:
        if tool == "send_email" and raw_output is not None:
            eid = getattr(raw_output, "id_", None)
            if eid is not None:
                self.sent_email_ids.append(str(eid))
        if tool in ("send_money", "schedule_transaction"):
            amt = N.parse_amount(args.get("amount")) or 0.0
            rcp = N.norm_value(args.get("recipient", ""))
            self.transfers.append((rcp, amt))
            self.cumulative_amount += amt
            self.transfers_per_recipient[rcp] += 1
        if sensitive:
            self.read_registry.append({"sid": sid, "tool": tool, "sensitive": sensitive})
