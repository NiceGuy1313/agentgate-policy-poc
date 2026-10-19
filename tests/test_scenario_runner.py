"""agentgate.scenarios.run: job selection, every framework x condition, per-run records and the summary (no network,
no LLM: scripted agent / fake LLM, oracle TaskSpec, offline ToolSpec)."""

import json

import pytest

from agentgate.harness import registry
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
