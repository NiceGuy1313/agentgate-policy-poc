"""판정 결합·정책 비활성화와 정책 패키지에 LLM·네트워크 import가 없음을 검사한다."""

import pathlib
import re

from agentgate.gate import Verdict
from agentgate.gate.base import PolicyDecision
from agentgate.gate.engine import PolicyEngine, combine_verdicts


def test_combine_verdicts_order_and_disable():
    decisions = [PolicyDecision(Verdict.ESCALATE, "P1", "x"), PolicyDecision(Verdict.BLOCK, "P2", "y")]
    assert combine_verdicts(decisions) == Verdict.BLOCK
    assert combine_verdicts(decisions, {"P2"}) == Verdict.ESCALATE
    assert combine_verdicts(decisions, {"P1", "P2"}) == Verdict.ALLOW


def test_disabled_policy_not_evaluated():
    assert "P3" not in [p.policy_id for p in PolicyEngine(disabled={"P3"}).policies]


def test_policy_package_has_no_llm_or_network_imports():
    policy_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "agentgate" / "policy"
    pattern = re.compile(r"\b(openai|anthropic|requests|httpx|urllib|socket|agentgate\.llm)\b")
    for path in policy_dir.glob("*.py"):
        assert not pattern.search(path.read_text()), path
