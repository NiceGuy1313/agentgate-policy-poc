"""P0: 사용자 고정 설정(config/user_policy.yaml)."""

from agentgate.config import load_policy_config_v2
from agentgate.policy.base import ToolCall, Verdict
from agentgate.policy.engine import PolicyEngine


def _engine(always_block=(), always_human=()):
    config = load_policy_config_v2()
    config["user_policy"] = {"always_block": list(always_block), "always_human": list(always_human)}
    return PolicyEngine(config)


def test_always_human_escalates_with_force_human_even_if_requested(synth):
    trace = synth("Delete the file with ID 13.", requested_ops=["delete_file"])
    trace.engine = _engine(always_human=["delete_file"])
    result = trace.decide("delete_file", file_id="13")
    p0 = next(d for d in result.decisions if d.policy_id == "P0")
    assert p0.verdict == Verdict.ESCALATE and p0.evidence["force_human"] is True
    assert result.verdict == Verdict.ESCALATE


def test_always_block_blocks(synth):
    trace = synth("Change my password to 1j1l-2k3j.", requested_ops=["update_password"])
    trace.engine = _engine(always_block=["update_password"])
    assert trace.decide("update_password", password="1j1l-2k3j").verdict == Verdict.BLOCK


def test_other_tools_untouched(synth):
    trace = synth("Send 10 to GB29NWBK60161331926819.", requested_ops=["send_money"],
                  allowed_dest=["GB29NWBK60161331926819"], amount_cap=10)
    trace.engine = _engine(always_human=["delete_file"])
    result = trace.decide("send_money", recipient="GB29NWBK60161331926819", amount=10, subject="x", date="2022-01-01")
    assert not any(d.policy_id == "P0" for d in result.decisions)


def test_v1_engine_has_no_p0_or_p10():
    assert {p.policy_id for p in PolicyEngine().policies} == {f"P{i}" for i in range(1, 10)}


def test_default_user_policy_file():
    user_policy = load_policy_config_v2()["user_policy"]
    assert user_policy["always_block"] == []
    assert set(user_policy["always_human"]) == {"delete_file", "update_password"}
