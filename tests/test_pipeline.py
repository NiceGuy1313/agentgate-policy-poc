"""실제 AgentDojo AgentPipeline 안의 정책 게이트를 검사한다(scripted 에이전트, LLM 없음)."""

from agentgate.agents.scripted_agent import ScriptedAgent
from agentgate.evaluation.replay import build_injections
from agentgate.pipeline import RecordingPipeline, build_pipeline, run_live_task
from agentgate.runtime import GatedRuntime
from agentgate.suites import load_suite
from agentgate.task_spec import load_task_spec


def _run(mode, escalate_action="block"):
    suite = load_suite("banking")
    user_task = suite.get_user_task_by_id("user_task_0")
    injection_task = suite.get_injection_task_by_id("injection_task_7")
    agent = ScriptedAgent("banking", user_task, injection_task)
    pipeline = RecordingPipeline(build_pipeline(
        agent, lambda p: (load_task_spec("oracle", "banking", "user_task_0"), {"source": "oracle"})))
    runtime = GatedRuntime(suite.tools, mode=mode, escalate_action=escalate_action, record_env_hash=True)
    result = run_live_task(suite, pipeline, user_task, injection_task,
                           build_injections("banking", user_task, injection_task), runtime)
    return result, runtime


def test_taskspec_element_configures_runtime_before_first_call():
    result, runtime = _run("audit")
    assert runtime.spec is not None and runtime.spec_meta["source"] == "oracle"
    assert result["security"] is True        # no defense: password changed


def test_enforce_blocks_password_change_and_agent_sees_message():
    result, runtime = _run("enforce")
    blocked = [event for event in runtime.trace if event.blocked]
    assert any(event.tool == "update_password" for event in blocked)
    assert result["security"] is False
    tool_messages = [m for m in result["messages"] if m["role"] == "tool" and m["tool"] == "update_password"]
    assert "blocked by security policy P6" in (tool_messages[0]["error"] or "")
