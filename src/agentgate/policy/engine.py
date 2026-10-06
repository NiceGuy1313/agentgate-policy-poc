from __future__ import annotations

from dataclasses import dataclass, field

from agentgate.config import policies_config
from agentgate.policy.base import RANK, Call, Context, Decision, Verdict
from agentgate.policy.rules import ALL_POLICIES


@dataclass
class EngineResult:
    verdict: Verdict
    decisions: list[Decision] = field(default_factory=list)

    @property
    def policy_ids(self) -> list[str]:
        return [d.policy_id for d in self.decisions if d.verdict != Verdict.ALLOW]

    @property
    def primary(self) -> Decision | None:
        hits = [d for d in self.decisions if d.verdict == self.verdict and d.verdict != Verdict.ALLOW]
        return hits[0] if hits else None

    def as_dict(self) -> dict:
        return {"verdict": self.verdict.value, "decisions": [d.as_dict() for d in self.decisions]}


def combine(decisions: list[Decision], disabled: set[str] = frozenset()) -> Verdict:
    """BLOCK > ESCALATE > ALLOW over the enabled policies' decisions."""
    v = Verdict.ALLOW
    for d in decisions:
        if d.policy_id in disabled:
            continue
        if RANK[d.verdict] > RANK[v]:
            v = d.verdict
    return v


class PolicyEngine:
    def __init__(self, config: dict | None = None, disabled: set[str] | None = None):
        self.config = config or policies_config()
        enabled = set(self.config.get("enabled", [])) - set(disabled or [])
        self.policies = [cls(self.config) for cls in ALL_POLICIES if cls.policy_id in enabled]

    def decide(self, call: Call, ctx: Context) -> EngineResult:
        decisions = []
        for p in self.policies:
            if p.applies_to(call.tool):
                d = p.evaluate(call, ctx)
                if d is not None:
                    decisions.append(d)
        return EngineResult(combine(decisions), decisions)


class AllowAllEngine:
    """Stage 2 stub."""

    config: dict = {}

    def decide(self, call: Call, ctx: Context) -> EngineResult:
        return EngineResult(Verdict.ALLOW, [])
