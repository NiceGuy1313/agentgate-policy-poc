import contextvars

import pytest
from agentdojo.functions_runtime import Function

from agentgate.policy.base import Decision, Verdict
from agentgate.policy.engine import AllowAllEngine, EngineResult
from agentgate.runtime import IN_GATE, GatedRuntime, env_hash
from agentgate.spec import TaskSpec, load_spec
from agentgate.suites import load_suite, task_env
from agentgate.trace import Origin


class BlockTool:
    def __init__(self, tool):
        self.tool, self.config = tool, {}

    def decide(self, call, ctx):
        if call.tool == self.tool:
            return EngineResult(Verdict.BLOCK, [Decision(Verdict.BLOCK, "PX", "stub block")])
        return EngineResult(Verdict.ALLOW, [])


def _run_gt(suite_name, ut_id, spec, engine, mode):
    s = load_suite(suite_name)
    ut = s.get_user_task_by_id(ut_id)
    env = task_env(s, ut)
    rt = GatedRuntime(s.tools, prompt=ut.PROMPT, spec=spec, engine=engine, mode=mode)
    for c in ut.ground_truth(env.model_copy(deep=True)):
        rt.run_function(env, c.function, c.args)
    return rt, env


def test_bill_payment_recipient_is_named_source():
    spec = load_spec("oracle", "banking", "user_task_0")
    rt, _ = _run_gt("banking", "user_task_0", spec, AllowAllEngine(), "audit")
    send = next(e for e in rt.trace if e.tool == "send_money")
    res = rt.index.origin_of(send.args["recipient"])
    assert res.origin == Origin.NAMED_SOURCE and res.tool == "read_file"


def test_enforce_block_does_not_touch_environment():
    s = load_suite("banking")
    ut = s.get_user_task_by_id("user_task_0")
    env = task_env(s, ut)
    rt = GatedRuntime(s.tools, prompt=ut.PROMPT, spec=TaskSpec(), engine=BlockTool("send_money"), mode="enforce")
    calls = ut.ground_truth(env.model_copy(deep=True))
    rt.run_function(env, calls[0].function, calls[0].args)
    before = env_hash(env)
    out, err = rt.run_function(env, calls[1].function, calls[1].args)
    assert env_hash(env) == before
    assert rt.trace[-1].blocked and not rt.trace[-1].executed
    assert "PX" in out and err is not None
    # raise_on_error path (GroundTruthPipeline) gets the message as the result, no exception
    out2, err2 = rt.run_function(env, calls[1].function, calls[1].args, raise_on_error=True)
    assert "PX" in out2 and err2 is None and env_hash(env) == before


def test_audit_mode_executes_and_records():
    s = load_suite("banking")
    ut = s.get_user_task_by_id("user_task_0")
    env = task_env(s, ut)
    before = env_hash(env)
    rt = GatedRuntime(s.tools, prompt=ut.PROMPT, spec=TaskSpec(), engine=BlockTool("send_money"), mode="audit")
    for c in ut.ground_truth(env.model_copy(deep=True)):
        rt.run_function(env, c.function, c.args)
    assert env_hash(env) != before
    assert rt.trace[-1].decision["verdict"] == "BLOCK" and rt.trace[-1].executed
    assert all(e.decide_ms >= 0 for e in rt.trace)


def test_unconfigured_runtime_fails_closed():
    s = load_suite("banking")
    rt = GatedRuntime(s.tools, mode="enforce")
    env = s.load_and_inject_default_environment({})
    out, err = rt.run_function(env, "get_balance", {})
    assert rt.trace[-1].blocked and err is not None


@pytest.mark.parametrize("suite_name", ["workspace", "travel", "banking", "slack"])
def test_no_tool_runs_outside_the_gate(monkeypatch, suite_name):
    """Every Function invocation during a full pipeline run happens inside GatedRuntime.run_function."""
    from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

    outside = []
    orig = Function.__call__

    def spy(self, *a, **k):
        if not IN_GATE.get():
            outside.append(self.name)
        return orig(self, *a, **k)

    monkeypatch.setattr(Function, "__call__", spy)
    s = load_suite(suite_name)
    factory = GatedRuntime.factory(spec=TaskSpec(), prompt="", engine=AllowAllEngine(), mode="audit")
    for ut in list(s.user_tasks.values())[:5]:
        s.run_task_with_pipeline(GroundTruthPipeline(ut), ut, None, {}, runtime_class=factory)
    assert outside == []
    assert factory.created and all(rt.trace for rt in factory.created)
