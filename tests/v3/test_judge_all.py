"""C8(judge_scope="all"): 판정 LLM이 모든 effect 호출과 엔진 플래그 호출을 최종 판단한다. LLM 호출 없음."""

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline  # noqa: F401  (agentdojo import order)

from agentgate import v3
from agentgate.agentdojo.suites import load_suite, load_task_environment
from agentgate.conditions import get_condition
from agentgate.gate.base import PolicyDecision, ToolCall, Verdict
from agentgate.gate.engine import EngineResult
from agentgate.gate.task_spec import TaskSpec
from agentgate.llm.client import Completion
from agentgate.v3 import judge as judge_module
from agentgate.v3.runtime import ToolSpecGatedRuntime, effect_tools


class FakeJudge:
    def __init__(self, verdict="BLOCK"):
        self.verdict, self.calls = verdict, []

    def __call__(self, call, result, context):
        self.calls.append((call.tool, result.verdict.value))
        return {"verdict": self.verdict, "final": self.verdict}


def _run(judge, scope, spec=None):
    suite = load_suite("banking")
    task = suite.get_user_task_by_id("user_task_0")
    env = load_task_environment(suite, task)
    runtime = ToolSpecGatedRuntime(suite.tools, prompt=task.PROMPT, spec=spec or TaskSpec(),
                                   engine=v3.make_engine(gate="v31"), escalate_action="judge", judge=judge,
                                   judge_scope=scope)
    for call in task.ground_truth(env.model_copy(deep=True)):
        runtime.run_function(env, call.function, call.args)
    return runtime


def test_all_scope_judges_every_effect_call_and_skips_allowed_reads():
    judge = FakeJudge("BLOCK")
    runtime = _run(judge, "all")
    judged = {tool for tool, _ in judge.calls}
    assert judged and judged <= effect_tools()
    for event in runtime.trace:
        if event.tool in effect_tools():
            assert event.blocked and not event.executed and event.decision["judge"]["verdict"] == "BLOCK"
        elif event.decision["verdict"] == "ALLOW":
            assert event.executed and "judge" not in event.decision


def test_all_scope_judge_can_allow_an_engine_block():
    runtime = _run(FakeJudge("ALLOW"), "all")
    assert all(event.executed for event in runtime.trace)


def test_escalate_scope_only_judges_escalations():
    judge = FakeJudge("ALLOW")
    runtime = _run(judge, "escalate")
    assert [v for _, v in judge.calls] == ["ESCALATE"] * len(judge.calls)
    assert all("judge" not in e.decision for e in runtime.trace if e.decision["verdict"] != "ESCALATE")


def test_user_always_block_is_not_judged():
    judge = FakeJudge("ALLOW")
    runtime = ToolSpecGatedRuntime((), prompt="x", spec=TaskSpec(), engine=v3.make_engine(gate="v31"),
                                   escalate_action="judge", judge=judge, judge_scope="all")
    result = EngineResult(Verdict.BLOCK, [PolicyDecision(Verdict.BLOCK, "P0", "always_block")])
    assert runtime.resolve(ToolCall("send_money", {}), result) == (Verdict.BLOCK, None)
    assert judge.calls == []


def test_binding_covers_the_same_effect_tools():
    assert set(v3.load_config("v31")["reversibility"]["irreversible"]) | set(
        v3.load_config("v31")["reversibility"]["reversible"]) == effect_tools()


def test_c8_condition():
    cond = get_condition("C8")
    assert (cond.gate, cond.judge, cond.escalate) == ("v31", "v3-all", "judge")


class StubClient:
    model = "stub-model"

    def __init__(self):
        self.requests = []

    def complete(self, system, user, max_tokens=4000, cache_system=False, cache_user_prefix=None):
        self.requests.append({"system": system, "user": user, "cache_system": cache_system,
                              "cache_user_prefix": cache_user_prefix})
        return Completion("BLOCK\nJ3", self.model, 1.0, 10, 2)


def test_all_scope_prompt_has_whole_rule_document_and_engine_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_module, "CACHE_DIR", tmp_path)
    client = StubClient()
    judge = judge_module.TrustedJudgeV3(client=client, gate="v31", scope="all")
    runtime = ToolSpecGatedRuntime((), prompt="Pay the bill.", spec=TaskSpec(requested_ops=["send_money"]),
                                   engine=v3.make_engine(gate="v31"))
    record = judge(ToolCall("send_money", {"recipient": "X", "amount": 1}), EngineResult(Verdict.ALLOW, []),
                   runtime.context, "Pay the bill.")
    request = client.requests[0]
    assert record["judge"] == "v3-all" and record["verdict"] == "BLOCK"
    for rule in v3.load_rules("v31"):
        assert f"- {rule['id']} [{rule['layer']}] {rule['title']}:" in request["system"]
    assert "policy engine verdict: ALLOW" in request["user"] and "no code rule flagged" in request["user"]
    assert "Rules indexed for this call" in request["user"]
    assert request["cache_system"] and request["user"].startswith(request["cache_user_prefix"])
    assert "## Input 2" not in request["cache_user_prefix"]
