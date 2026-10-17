"""정책 엔진: 활성화된 정책을 툴 호출에 적용하고 판정을 결합한다.

어떤 정책 클래스를 쓰는지는 버전 패키지가 정한다(`agentgate.v1`·`v2`·`v3`의 `make_engine`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentgate.gate.base import PolicyContext, PolicyDecision, ToolCall, Verdict, strictest_verdict


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
    """`policy_classes` is the version's ordered policy list; omitted, the v1 gate (config/v1/policies.yaml, P1–P9).

    The order matters: the first decision with the combined verdict is the primary decision (block message).
    """

    def __init__(self, config: dict | None = None, disabled: set[str] | None = None, policy_classes=None):
        if policy_classes is None:
            from agentgate import v1
            if config is not None and config.get("version") != v1.VERSION:
                raise ValueError(f"config version {config.get('version')!r} needs that version's policy classes "
                                 "(use agentgate.<version>.make_engine)")
            config, policy_classes = config or v1.load_config(), v1.POLICY_CLASSES
        self.config = config
        enabled = set(self.config.get("enabled", [])) - set(disabled or [])
        self.policies = [cls(self.config) for cls in policy_classes if cls.policy_id in enabled]

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
