"""Gate를 우회한 무효 run(valid: false)이 하류 분석 스크립트의 점수·쌍 비교에서 빠지는지 검사한다(LLM·네트워크 없음)."""

import json
import sys

import pytest

from agentgate.config import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

import analyze_live_utility  # noqa: E402
import live_significance  # noqa: E402
import summarize_results  # noqa: E402


def _write(run_dir, condition, injection_task, **fields):
    record = {"condition": condition, "suite": "banking", "user_task": "user_task_0",
              "injection_task": injection_task, "agent_model": "scripted", "utility": True, "security": False,
              "trace": [], "valid": True, **fields}
    path = run_dir / condition / "banking" / "user_task_0" / f"{injection_task or 'none'}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))


@pytest.fixture()
def run_dir(tmp_path):
    """no_gate: 공격 run 둘. gate_static_rules: injection_task_1은 Gate 우회로 무효(점수는 None으로 비워진다)."""
    for injection_task in ("injection_task_0", "injection_task_1"):
        _write(tmp_path, "no_gate", injection_task, security=True)
    _write(tmp_path, "gate_static_rules", "injection_task_0")
    _write(tmp_path, "gate_static_rules", "injection_task_1", valid=False, utility=None, security=None,
           invalid_reason="ungated tool execution (policy gate bypassed): send_money")
    return tmp_path


def test_live_significance_skips_invalid_runs(run_dir):
    runs, n_invalid = live_significance.load_runs(run_dir)
    assert n_invalid == 1 and len(runs) == 3
    result = live_significance.analyse(run_dir, n_boot=10)
    assert result["n_invalid"] == 1
    asr = result["conditions"]["gate_static_rules"]["asr"]
    assert (asr["n"], asr["k"]) == (1, 0)                       # 무효 run은 분모에도 들어가지 않는다
    assert asr["paired_vs_baseline"]["n_pairs"] == 1            # 쌍 단위로 제외한다


def test_utility_analysis_skips_invalid_runs(run_dir):
    runs, n_invalid = analyze_live_utility.load_condition(run_dir, "gate_static_rules", run_dir / "tmp")
    assert n_invalid == 1 and list(runs) == [("banking", "user_task_0", "injection_task_0")]


def test_results_summary_skips_invalid_runs(run_dir):
    runs = summarize_results.load_valid_runs(run_dir)
    assert len(runs) == 3 and all(r.get("valid") is not False for r in runs)
