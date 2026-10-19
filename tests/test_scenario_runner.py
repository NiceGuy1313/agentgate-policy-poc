"""agentgate.scenarios.run 테스트: 작업 선택, 프레임워크 x 조건 전체, run별 레코드와 요약.

네트워크·LLM 없음: scripted agent / 가짜 LLM, oracle TaskSpec, offline ToolSpec.
"""

import json

import pytest

from agentgate.harness import bridge, registry
from agentgate.harness.conformance import offline_toolspec
from agentgate.scenarios import run, runner


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    class InlinePool:   # the scripted native path uses a process pool; keep it in-process (offline ToolSpec patch)
        def __init__(self, workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def imap_unordered(self, fn, jobs, chunksize=1):
            return map(fn, jobs)

    monkeypatch.setattr(runner.mp, "Pool", InlinePool)
    monkeypatch.delenv("AGENTGATE_TASKSPEC_CACHE_DIR", raising=False)
    with offline_toolspec():
        yield


def test_pair_selection():
    assert runner.select_pairs(["banking"], ["user_task_0"], []) == [("banking", "user_task_0", None)]
    assert runner.select_pairs(["banking"], ["user_task_0"], ["injection_task_1"], benign=False) == [
        ("banking", "user_task_0", "injection_task_1")]
    assert runner.select_pairs(["banking"], ["user_task_0"], ["none", "injection_task_1"], benign=False) == [
        ("banking", "user_task_0", None), ("banking", "user_task_0", "injection_task_1")]
    every = runner.select_pairs(["banking"], ["user_task_0"], None)
    assert every[0] == ("banking", "user_task_0", None) and len(every) == 1 + 9
    assert runner.parse_pairs("banking:user_task_0:none,slack:user_task_1:injection_task_2") == [
        ("banking", "user_task_0", None), ("slack", "user_task_1", "injection_task_2")]


@pytest.mark.parametrize("framework", ["native", "dummy"])
def test_cli_runs_every_condition_for_a_framework(tmp_path, monkeypatch, framework):
    registry.reset()
    monkeypatch.setattr(registry, "BUILTIN", {**registry.BUILTIN,
                                              "dummy": "tests.frameworks.dummy_adapter:DummyAdapter"})
    try:
        summary = run.main(["--framework", framework, "--agent", "scripted", "--spec", "oracle",
                            "--condition", "no_gate", "gate_static_rules", "--suite", "banking",
                            "--user-task", "user_task_0", "--injection-task", "injection_task_0",
                            "--attack", "tool_knowledge", "--workers", "1", "--out", str(tmp_path)])
    finally:
        registry.reset()
    for condition in ("no_gate", "gate_static_rules"):
        record = json.loads((tmp_path / condition / "banking" / "user_task_0" / "injection_task_0.json").read_text())
        assert record["valid"] is True and not record.get("error") and record["attack"] == "tool_knowledge"
        assert summary["conditions"][condition]["scored_runs"] == 1
    assert summary["conditions"]["gate_static_rules"]["blocked_calls"] >= 1
    assert summary["conditions"]["gate_static_rules"]["asr"] == 0.0
    assert summary["conditions"]["no_gate"]["asr"] == 1.0
    assert json.loads((tmp_path / runner.SUMMARY_FILE).read_text()) == summary
    assert list(tmp_path.glob("run_*.json"))


def test_native_runs_through_the_bridge_like_every_framework(tmp_path, monkeypatch):
    """native도 Adapter -> GateToolBridge -> Session 경로다: 툴 실행마다 bridge를 지나고 레코드에 harness 필드가 있다."""
    bridged = []
    invoke_detail = bridge.GateToolBridge.invoke_detail

    def counting(self, tool, args):
        bridged.append(tool)
        return invoke_detail(self, tool, args)

    monkeypatch.setattr(bridge.GateToolBridge, "invoke_detail", counting)
    run.main(["--framework", "native", "--agent", "scripted", "--spec", "oracle", "--condition", "gate_static_rules",
              "--suite", "banking", "--user-task", "user_task_0", "--injection-task", "injection_task_0",
              "--attack", "tool_knowledge", "--workers", "1", "--out", str(tmp_path)])
    record = json.loads((tmp_path / "gate_static_rules" / "banking" / "user_task_0" / "injection_task_0.json")
                        .read_text())
    assert record["framework"] == "native" and record["manifest"]["framework"] == "native"
    assert bridged and bridged == [e["tool"] for e in record["trace"]]
    assert record["valid"] is True and not record["ungated_calls"]
    # summarize_live / check_enforcement / 대시보드가 읽는 필드
    assert {"utility", "security", "security_executed", "final_env_hash", "messages", "spec", "spec_meta", "judge",
            "gate_ms", "agent", "agent_model"} <= set(record)
    assert {"input", "output", "calls", "refusals", "llm_ms"} <= set(record["agent_tokens"])


def test_scripted_on_block_stop_ends_the_run_after_a_block(tmp_path):
    def blocked_run(on_block):
        out = tmp_path / on_block
        run.main(["--framework", "native", "--agent", "scripted", "--spec", "oracle", "--on-block", on_block,
                  "--condition", "gate_static_rules", "--suite", "banking", "--user-task", "user_task_0",
                  "--injection-task", "injection_task_0", "--attack", "tool_knowledge", "--workers", "1",
                  "--out", str(out)])
        record = json.loads((out / "gate_static_rules" / "banking" / "user_task_0" / "injection_task_0.json")
                            .read_text())
        return [(e["tool"], e["blocked"]) for e in record["trace"]]

    keep_going, stopped = blocked_run("continue"), blocked_run("stop")
    assert any(blocked for _, blocked in stopped) and len(stopped) < len(keep_going)
    assert stopped == keep_going[:len(stopped)] and stopped[-1][1]   # 첫 차단에서 끝난다


def test_usage_limit_leaves_no_record_and_stops_later_jobs(tmp_path, monkeypatch):
    attempts = []

    def limit(request, sink=None):
        attempts.append(request)
        raise RuntimeError("429 You used all included usage")

    monkeypatch.setattr("agentgate.harness.runner.execute", limit)
    runner.reset_quota_flag()
    try:
        jobs = runner.build_jobs(["no_gate"], [("banking", "user_task_0", None), ("banking", "user_task_2", None)],
                                 agent="llm", spec="oracle", attack="tool_knowledge", attack_model_name="AI assistant",
                                 on_block="continue", out=str(tmp_path), framework="native", framework_options="{}",
                                 model="ollama:gpt-oss:120b", max_steps=None)
        assert [runner.run_one(job) for job in jobs] == ["quota", "quota"]
    finally:
        runner.reset_quota_flag()
    assert len(attempts) == 1                                  # 한도에 걸린 뒤의 작업은 시도하지 않는다
    assert not list(tmp_path.glob("*/*/*/*.json"))             # 다음 실행이 다시 시도하도록 레코드를 남기지 않는다


def test_user_task_without_injection_is_a_single_benign_run(tmp_path):
    run.main(["--agent", "scripted", "--spec", "oracle", "--condition", "gate_static_rules", "--suite", "banking",
              "--user-task", "user_task_2", "--workers", "1", "--out", str(tmp_path)])
    assert [p.name for p in tmp_path.glob("*/*/*/*.json")] == ["none.json"]


def test_summary_excludes_invalid_runs():
    base = {"condition": "gate_static_rules", "suite": "banking", "user_task": "user_task_0", "trace": []}
    records = [{**base, "injection_task": "injection_task_0", "security": False, "utility": True,
                "security_executed": False, "valid": True},
               {**base, "injection_task": "injection_task_1", "security": None, "utility": None,
                "security_executed": None, "valid": False, "invalid_reason": "ungated tool execution: send_money"}]
    summary = runner.summarize(records)["conditions"]["gate_static_rules"]
    assert summary["scored_runs"] == 1 and summary["invalid_runs"] == 1 and summary["asr"] == 0.0
    assert summary["invalid"] == [{"pair": "banking/user_task_0/injection_task_1",
                                   "reason": "ungated tool execution: send_money"}]
