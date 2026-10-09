"""`GatedRuntime`의 enforce·audit 동작, fail-closed, 게이트 밖 툴 실행 부재를 검사한다."""

import pytest
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.functions_runtime import Function

from agentgate.policy.base import PolicyDecision, Verdict
from agentgate.policy.engine import AllowAllPolicyEngine, EngineResult
from agentgate.provenance import Origin
from agentgate.runtime import INSIDE_GATE, GatedRuntime, compute_environment_hash
from agentgate.suites import load_suite, load_task_environment
from agentgate.task_spec import TaskSpec, load_task_spec


class BlockTool:
    def __init__(self, tool):
        self.tool, self.config = tool, {}

    def decide(self, call, context):
        if call.tool == self.tool:
            return EngineResult(Verdict.BLOCK, [PolicyDecision(Verdict.BLOCK, "PX", "stub block")])
        return EngineResult(Verdict.ALLOW, [])


def _run_ground_truth(suite_name, user_task_id, spec, engine, mode):
    suite = load_suite(suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)
    environment = load_task_environment(suite, user_task)
    runtime = GatedRuntime(suite.tools, prompt=user_task.PROMPT, spec=spec, engine=engine, mode=mode)
    for call in user_task.ground_truth(environment.model_copy(deep=True)):
        runtime.run_function(environment, call.function, call.args)
    return runtime, environment


def test_bill_payment_recipient_is_named_source():
    spec = load_task_spec("oracle", "banking", "user_task_0")
    runtime, _ = _run_ground_truth("banking", "user_task_0", spec, AllowAllPolicyEngine(), "audit")
    send = next(event for event in runtime.trace if event.tool == "send_money")
    provenance = runtime.index.origin_of(send.args["recipient"])
    assert provenance.origin == Origin.NAMED_SOURCE and provenance.tool == "read_file"


def test_enforce_block_does_not_touch_environment():
    suite = load_suite("banking")
    user_task = suite.get_user_task_by_id("user_task_0")
    environment = load_task_environment(suite, user_task)
    runtime = GatedRuntime(suite.tools, prompt=user_task.PROMPT, spec=TaskSpec(), engine=BlockTool("send_money"),
                           mode="enforce")
    calls = user_task.ground_truth(environment.model_copy(deep=True))
    runtime.run_function(environment, calls[0].function, calls[0].args)
    before = compute_environment_hash(environment)
    output, error = runtime.run_function(environment, calls[1].function, calls[1].args)
    assert compute_environment_hash(environment) == before
    assert runtime.trace[-1].blocked and not runtime.trace[-1].executed
    assert "PX" in output and error is not None
    # raise_on_error path (GroundTruthPipeline) gets the message as the result, no exception
    output, error = runtime.run_function(environment, calls[1].function, calls[1].args, raise_on_error=True)
    assert "PX" in output and error is None and compute_environment_hash(environment) == before


def test_audit_mode_executes_and_records():
    suite = load_suite("banking")
    user_task = suite.get_user_task_by_id("user_task_0")
    environment = load_task_environment(suite, user_task)
    before = compute_environment_hash(environment)
    runtime = GatedRuntime(suite.tools, prompt=user_task.PROMPT, spec=TaskSpec(), engine=BlockTool("send_money"),
                           mode="audit")
    for call in user_task.ground_truth(environment.model_copy(deep=True)):
        runtime.run_function(environment, call.function, call.args)
    assert compute_environment_hash(environment) != before
    assert runtime.trace[-1].decision["verdict"] == "BLOCK" and runtime.trace[-1].executed
    assert all(event.decide_ms >= 0 for event in runtime.trace)


def test_unconfigured_runtime_fails_closed():
    suite = load_suite("banking")
    runtime = GatedRuntime(suite.tools, mode="enforce")
    environment = suite.load_and_inject_default_environment({})
    _, error = runtime.run_function(environment, "get_balance", {})
    assert runtime.trace[-1].blocked and error is not None


@pytest.mark.parametrize("suite_name", ["workspace", "travel", "banking", "slack"])
def test_no_tool_runs_outside_the_gate(monkeypatch, suite_name):
    """Every Function invocation during a full pipeline run happens inside GatedRuntime.run_function."""
    outside = []
    original_call = Function.__call__

    def spy(self, *args, **kwargs):
        if not INSIDE_GATE.get():
            outside.append(self.name)
        return original_call(self, *args, **kwargs)

    monkeypatch.setattr(Function, "__call__", spy)
    suite = load_suite(suite_name)
    factory = GatedRuntime.factory(spec=TaskSpec(), prompt="", engine=AllowAllPolicyEngine(), mode="audit")
    for user_task in list(suite.user_tasks.values())[:5]:
        suite.run_task_with_pipeline(GroundTruthPipeline(user_task), user_task, None, {}, runtime_class=factory)
    assert outside == []
    assert factory.created and all(runtime.trace for runtime in factory.created)
