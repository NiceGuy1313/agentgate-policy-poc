"""실제 AgentDojo AgentPipeline 안의 정책 Gate를 검사한다(scripted 에이전트, 룰북 정적 코드, LLM 없음)."""

from agentgate.agents.scripted_agent import ScriptedAgent
from agentgate.policy_gate.rule_checker import RuleChecker
from agentgate.policy_gate.runtime import PolicyGateRuntime
from agentgate.scenarios.pipeline import RecordingPipeline, build_pipeline, run_live_task
from agentgate.scenarios.replay import build_injections
from agentgate.scenarios.suites import load_suite
from agentgate.scenarios.task_specs import load_task_spec


def _run(mode):
    suite = load_suite("banking")
    user_task = suite.get_user_task_by_id("user_task_0")
    injection_task = suite.get_injection_task_by_id("injection_task_7")
    agent = ScriptedAgent("banking", user_task, injection_task)
    pipeline = RecordingPipeline(build_pipeline(
        agent, lambda p: (load_task_spec("oracle", "banking", "user_task_0"), {"source": "oracle"})))
    runtime = PolicyGateRuntime(suite.tools, mode=mode, judge=RuleChecker() if mode == "enforce" else None, toolspec=lambda tool, args, text: {"records": [], "requests": []},
                                record_env_hash=True)
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
    message = tool_messages[0]["error"] or ""
    judgement = next(e for e in blocked if e.tool == "update_password").decision["judge"]
    assert "R6" in judgement["rule_ids"]
    # the agent sees the judge's rule ids and reason, not the judge record
    assert message.startswith(f"Tool call blocked by policy gate ({', '.join(judgement['rule_ids'])}): ")
    assert "not requested" in message and "cached" not in message and "{" not in message
