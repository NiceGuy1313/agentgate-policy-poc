"""판정기 인터페이스와 등록부: 판정기는 (call, context) -> 판정 기록(dict)이다.

판정 기록의 "verdict"는 "ALLOW" | "BLOCK"이고, 나머지 필드(judge, rule_ids, reason, text, 토큰 수 등)는 판정기마다 다르며
trace의 "judge"에 그대로 남는다. 조건(`agentgate.experiment.conditions.Condition.judge`)은 이 등록부의 이름으로 판정기를 고른다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agentgate.core.types import PolicyContext, ToolCall


class Judge(Protocol):
    kind: str   # name in the judge record ("rules" | "llm" | "llm-norules")

    def __call__(self, call: ToolCall, context: PolicyContext) -> dict: ...


def _rules() -> Judge:
    from agentgate.policy_gate.rule_checker import RuleChecker
    return RuleChecker()


def _llm(use_rules: bool) -> Callable[[], Judge]:
    def make() -> Judge:
        from agentgate.policy_gate.llm_judge import LLMJudge
        return LLMJudge(use_rules=use_rules)
    return make


JUDGES: dict[str, Callable[[], Judge]] = {"rules": _rules, "llm": _llm(True), "llm-norules": _llm(False)}


def register_judge(name: str, factory: Callable[[], Judge]) -> None:
    """Add (or replace) a judge factory under `name`."""
    JUDGES[name] = factory


def make_judge(name: str) -> Judge:
    if name not in JUDGES:
        raise ValueError(f"unknown judge {name!r} (known: {', '.join(JUDGES)})")
    return JUDGES[name]()
