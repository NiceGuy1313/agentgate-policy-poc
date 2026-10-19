"""정책 엔진 결과형과, 모든 호출을 허용하는 기본 엔진이다. 실제 판정은 `GatedRuntime.resolve`(policy_gate)가 한다."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentgate.core.types import PolicyContext, PolicyDecision, ToolCall, Verdict


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


class AllowAllPolicyEngine:
    """Engine that allows every call (the gate's default; decisions come from the runtime's judge)."""

    config: dict = {}

    def decide(self, call: ToolCall, context: PolicyContext) -> EngineResult:
        return EngineResult(Verdict.ALLOW, [])
