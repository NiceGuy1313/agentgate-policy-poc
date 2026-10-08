"""Policy engine: runs the enabled policies on a tool call and combines their verdicts."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentgate.config import load_policy_config
from agentgate.policy.base import PolicyContext, PolicyDecision, ToolCall, Verdict, strictest_verdict
from agentgate.policy.rules import POLICY_CLASSES


@dataclass
class EngineResult:
    verdict: Verdict
    decisions: list[PolicyDecision] = field(default_factory=list)

    @property
    def flagged_policy_ids(self) -> list[str]:
        return [d.policy_id for d in self.decisions if d.verdict != Verdict.ALLOW]

    @property
    def primary_decision(self) -> PolicyDecision | None:
        hits = [d for d in self.decisions if d.verdict == self.verdict and d.verdict != Verdict.ALLOW]
        return hits[0] if hits else None

    def as_dict(self) -> dict:
        return {"verdict": self.verdict.value, "decisions": [d.as_dict() for d in self.decisions]}


def combine_verdicts(decisions: list[PolicyDecision], disabled: set[str] = frozenset()) -> Verdict:
    """BLOCK > ESCALATE > ALLOW over the enabled policies' decisions."""
    return strictest_verdict(d.verdict for d in decisions if d.policy_id not in disabled)


class PolicyEngine:
    def __init__(self, config: dict | None = None, disabled: set[str] | None = None):
        self.config = config or load_policy_config()
        enabled = set(self.config.get("enabled", [])) - set(disabled or [])
        self.policies = [cls(self.config) for cls in POLICY_CLASSES if cls.policy_id in enabled]

    def decide(self, call: ToolCall, context: PolicyContext) -> EngineResult:
        decisions = []
        for policy in self.policies:
            if policy.applies_to(call.tool):
                decision = policy.evaluate(call, context)
                if decision is not None:
                    decisions.append(decision)
        return EngineResult(combine_verdicts(decisions), decisions)


class AllowAllPolicyEngine:
    """Engine that allows every call (used to record traces without enforcement)."""

    config: dict = {}

    def decide(self, call: ToolCall, context: PolicyContext) -> EngineResult:
        return EngineResult(Verdict.ALLOW, [])
