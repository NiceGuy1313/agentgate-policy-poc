"""v1 실험 조건(C0–C4) 런타임 조립."""

from __future__ import annotations

from agentgate.gate.runtime import GatedRuntime
from agentgate.v1 import make_engine


def build(cond, *, suite_name: str, prompt: str, escalate: str, disabled=None, stats=None):
    kwargs = {"engine": make_engine(disabled)}
    if escalate == "judge":
        from agentgate.v1.judge import EscalationJudge, as_runtime_judge
        judge = EscalationJudge()
        kwargs["judge"] = as_runtime_judge(judge, prompt)
        if stats is not None:
            stats["judge_object"] = judge   # live records read judge.stats
    return GatedRuntime, kwargs
