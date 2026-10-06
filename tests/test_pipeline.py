"""The policy gate inside a real AgentDojo AgentPipeline (scripted agent, no LLM)."""

from agentgate.pipeline import RecordingPipeline, ScriptedAgent, build_pipeline, run_live_task
from agentgate.replay import injections_for
from agentgate.runtime import GatedRuntime
from agentgate.spec import load_spec
from agentgate.suites import load_suite


def _run(cond_mode, esc="block"):
    s = load_suite("banking")
    ut, it = s.get_user_task_by_id("user_task_0"), s.get_injection_task_by_id("injection_task_7")
    agent = ScriptedAgent("banking", ut, it)
    pipe = RecordingPipeline(build_pipeline(agent, lambda p: (load_spec("oracle", "banking", "user_task_0"), {"source": "oracle"})))
    rt = GatedRuntime(s.tools, mode=cond_mode, escalate_action=esc, record_env_hash=True)
    res = run_live_task(s, pipe, ut, it, injections_for("banking", ut, it), rt)
    return res, rt


def test_taskspec_element_configures_runtime_before_first_call():
    res, rt = _run("audit")
    assert rt.spec is not None and rt.spec_meta["source"] == "oracle"
    assert res["security"] is True        # no defense: password changed


def test_enforce_blocks_password_change_and_agent_sees_message():
    res, rt = _run("enforce")
    blocked = [e for e in rt.trace if e.blocked]
    assert any(e.tool == "update_password" for e in blocked)
    assert res["security"] is False
    tool_msgs = [m for m in res["messages"] if m["role"] == "tool" and m["tool"] == "update_password"]
    assert "blocked by security policy P6" in (tool_msgs[0]["error"] or "")
