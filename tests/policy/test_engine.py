from agentgate.policy import Verdict
from agentgate.policy.engine import PolicyEngine, combine
from agentgate.policy.base import Decision


def test_combine_order_and_disable():
    ds = [Decision(Verdict.ESCALATE, "P1", "x"), Decision(Verdict.BLOCK, "P2", "y")]
    assert combine(ds) == Verdict.BLOCK
    assert combine(ds, {"P2"}) == Verdict.ESCALATE
    assert combine(ds, {"P1", "P2"}) == Verdict.ALLOW


def test_disabled_policy_not_evaluated():
    assert "P3" not in [p.policy_id for p in PolicyEngine(disabled={"P3"}).policies]


def test_policy_package_has_no_llm_or_network_imports():
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "agentgate" / "policy"
    pat = re.compile(r"\b(openai|anthropic|requests|httpx|urllib|socket|agentgate\.llm)\b")
    for f in src.glob("*.py"):
        assert not pat.search(f.read_text()), f
