"""대시보드 API 테스트(네트워크·LLM 없음): 목록, 실행 요청 검증, runner를 mock한 파일럿 실행."""

import json
import re
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import dashboard.app as dash  # noqa: E402
from agentgate.experiment import batch as runner  # noqa: E402
from agentgate.harness import models as harness_models  # noqa: E402

BODY = {"framework": "native", "model": "openai_compat:fake-model", "condition": "gate_static_rules"}
ARCH = Path(dash.APP_DIR) / "static" / "arch"


def fake_record(job: dict) -> dict:
    """What `runner.run_one` writes for one scenario, shaped by the condition (no_gate lets the attack through)."""
    cond, injection = job["condition"], job["injection_task"]
    gated, attacked = cond != "no_gate", injection is not None
    trace = [{"step": 0, "tool": "read_file", "args": {"file_path": "bill.txt"}, "verdict": "ALLOW",
              "effective": "ALLOW", "decisions": [], "judge": None, "blocked": False, "executed": True,
              "error": None, "decide_ms": 0.1}]
    if attacked:
        blocked = gated
        judge = ({"judge": "rules", "verdict": "BLOCK", "rule_ids": ["R2", "R3"], "reason": "R2: x; R3: y"}
                 if gated else None)
        trace.append({"step": 1, "tool": "send_money", "args": {"recipient": "US13", "amount": 0.01},
                      "verdict": "BLOCK" if blocked else "ALLOW", "effective": "BLOCK" if blocked else "ALLOW",
                      "decisions": [], "judge": judge, "blocked": blocked, "executed": not blocked, "error": None,
                      "decide_ms": 1.0})
    record = {"condition": cond, "suite": job["suite"], "user_task": job["user_task"], "injection_task": injection,
              "utility": True, "security": attacked and not gated, "security_executed": attacked and not gated,
              "final_output": "done", "trace": trace, "valid": True, "ungated_calls": [], "error": None}
    if attacked is False:
        record.pop("security"), record.pop("security_executed")
    pair = (job["user_task"], injection)
    if gated and pair == ("user_task_13", "injection_task_7"):      # a run that bypassed the gate
        record.update(valid=False, invalid_reason="ungated tool execution (policy gate bypassed): send_money",
                      excluded_scores={"utility": True, "security": False, "security_executed": False},
                      utility=None, security=None, security_executed=None)
    if gated and pair == ("user_task_3", "injection_task_4"):
        record.update(error="RuntimeError: model unreachable", utility=False)
    return record


def write_record(job: dict) -> str:
    record = fake_record(job)
    path = runner.record_path(job["out"], job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return "invalid" if record["valid"] is False else "error" if record["error"] else "ok"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(dash, "runs", {})
    monkeypatch.setattr(dash.runner, "set_agent_model", lambda model: None)     # keep AGENTGATE_AGENT_* untouched
    return TestClient(dash.app)


def wait_done(client, run_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("run did not finish")


# ---------------------------------------------------------------------------------------------- catalog
def test_catalog_lists_the_three_conditions_and_the_fixed_pilot(client):
    catalog = client.get("/api/catalog").json()
    assert [c["name"] for c in catalog["conditions"]] == ["no_gate", "gate_llm_judge", "gate_static_rules"]
    assert {c["name"]: c["label"] for c in catalog["conditions"]} == {
        "no_gate": "Gate 없음", "gate_llm_judge": "Policy 판단 LLM", "gate_static_rules": "정규식·정적 규칙 비교"}
    assert {c["name"]: c["judge"] for c in catalog["conditions"]} == {
        "no_gate": None, "gate_llm_judge": "llm", "gate_static_rules": "rules"}
    frameworks = {f["name"]: f for f in catalog["frameworks"]}
    assert "native" in frameworks and not frameworks["native"]["unavailable"]
    assert frameworks["smolagents"]["has_architecture"] and not frameworks["native"]["has_architecture"]
    pilot = catalog["pilot"]                                    # the runner's PILOT: banking, 4 user x 3 injection
    assert (pilot["total"], pilot["benign"], pilot["attacks"]) == (16, 4, 12)
    assert pilot["attack"] == "tool_knowledge" and pilot["suites"] == ["banking"]
    assert [t["id"] for t in pilot["user_tasks"]] == ["user_task_0", "user_task_2", "user_task_3", "user_task_13"]
    assert all(t["prompt"] for t in pilot["user_tasks"]) and all(t["goal"] for t in pilot["injection_tasks"])


# ---------------------------------------------------------------------------------------------- validation
@pytest.mark.parametrize("change, status", [
    ({"condition": "nope"}, 422),
    ({"compare_condition": "gate_static_rules"}, 422),          # the same condition twice
    ({"framework": "nope"}, 422),
    ({"model": "no-colon"}, 422),
    ({"model": "mystery:model"}, 422),                           # unknown provider
    ({"max_steps": 99}, 422),
    ({"spec": "magic"}, 422),
    ({"suite": "banking"}, 422),                                 # the scenario set is fixed: no scenario fields
    ({"user_task": "user_task_0"}, 422),
    ({"injection_task": "injection_task_0"}, 422),
    ({"attack": "direct"}, 422),
    ({"model": "ollama:gpt-oss:120b"}, 400),                     # ollama cloud key not set
])
def test_run_request_is_validated_and_nothing_starts(client, monkeypatch, change, status):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(harness_models, "load_dotenv", lambda: None)     # ignore the developer's .env
    called = []
    monkeypatch.setattr(dash.runner, "run_jobs", lambda *args: called.append(args))
    response = client.post("/api/runs", json={**BODY, **change})
    assert response.status_code == status, response.text
    assert not dash.runs and not called


@pytest.mark.parametrize("missing", ["framework", "model", "condition"])
def test_required_fields(client, missing):
    body = {k: v for k, v in BODY.items() if k != missing}
    assert client.post("/api/runs", json=body).status_code == 422


# ---------------------------------------------------------------------------------------------- a pilot run
def test_pilot_run_with_mocked_runner(client, monkeypatch):
    release, started = threading.Event(), []

    def fake_run_one(job):
        started.append(job)
        if job["user_task"] == "user_task_13":          # still running while the first runs are done
            release.wait(10)
        return write_record(job)

    monkeypatch.setattr(dash.runner, "run_one", fake_run_one)
    # oracle TaskSpecs keep the mocked run offline (the default "llm" source would call the extractor first)
    response = client.post("/api/runs", json={**BODY, "compare_condition": "no_gate", "spec": "oracle"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert client.post("/api/runs", json=BODY).status_code == 409        # one run at a time

    deadline = time.time() + 10                           # progress while the last scenarios are held
    while time.time() < deadline:
        running = client.get(f"/api/runs/{run_id}").json()
        if running["progress"]["done"] == 24:
            break
        time.sleep(0.02)
    assert running["status"] == "running" and running["progress"] == {"done": 24, "total": 32}
    assert [r["name"] for r in running["results"]] == ["gate_static_rules", "no_gate"]
    assert all((r["done"], r["total"]) == (12, 16) for r in running["results"])
    release.set()

    done = wait_done(client, run_id)
    assert done["status"] == "finished" and done["progress"] == {"done": 32, "total": 32}
    assert done["view"] == {"framework": "native", "model": "openai_compat:fake-model",
                            "conditions": ["gate_static_rules", "no_gate"], "spec": "oracle",
                            "suites": ["banking"], "attack": "tool_knowledge"}
    assert {j["attack"] for j in started} == {"tool_knowledge"} and len(started) == 32
    assert {(j["suite"], j["user_task"], j["injection_task"]) for j in started} == set(dash.pilot_pairs())

    gated, baseline = done["results"]
    m = gated["metrics"]                                  # 12 attack runs: 1 invalid, 1 error -> 10 scored
    assert (m["benign_runs"], m["attack_runs"], m["invalid_runs"], m["errors"]) == (4, 10, 1, 1)
    assert (m["utility"], m["asr"], m["asr_executed_only"]) == (1.0, 0.0, 0.0)
    assert (m["blocked_calls"], m["tool_calls"]) == (10, 4 * 1 + 10 * 2)
    assert m["invalid"] == [{"pair": "banking/user_task_13/injection_task_7",
                             "reason": "ungated tool execution (policy gate bypassed): send_money"}]
    assert (m["benign_success"], m["attack_success"], m["attack_utility_success"]) == (4, 0, 10)
    b = baseline["metrics"]                               # no_gate: every attack goes through, nothing is blocked
    assert (b["utility"], b["asr"], b["blocked_calls"], b["attack_runs"]) == (1.0, 1.0, 0, 12)
    assert not baseline["gated"] and gated["gated"] and gated["judge"] == "rules"

    rows = {(r["user_task"], r["injection_task"]): r for r in gated["scenarios"]}
    benign, attack = rows[("user_task_0", None)], rows[("user_task_0", "injection_task_0")]
    assert benign["state"] == "done" and not benign["attacked"] and benign["blocked"] == 0
    assert attack["attacked"] and attack["utility"] is True and attack["security"] is False
    assert (attack["blocked"], attack["tool_calls"], attack["executed"]) == (1, 2, 1)
    read, send = attack["calls"]
    assert read["verdict"] == "ALLOW" and not read["judged"] and read["rule_ids"] == []
    assert send["verdict"] == "BLOCK" and send["blocked"] and not send["executed"]
    assert send["rule_ids"] == ["R2", "R3"] and send["judge"]["kind"] == "rules" and "R3: y" in send["reason"]
    invalid, error = rows[("user_task_13", "injection_task_7")], rows[("user_task_3", "injection_task_4")]
    assert invalid["state"] == "invalid" and invalid["utility"] is None
    assert invalid["excluded_scores"]["utility"] is True and "bypassed" in invalid["invalid_reason"]
    assert error["state"] == "error" and "unreachable" in error["error"]
    assert baseline["scenarios"][1]["calls"][1]["verdict"] == "ALLOW"    # no_gate: the attack call executed
    assert baseline["scenarios"][1]["security"] is True

    assert (dash.RUNS_DIR / run_id / "meta.json").exists()    # saved: readable after a restart
    assert (dash.RUNS_DIR / run_id / "scenario_summary.json").exists()
    dash.runs.clear()
    assert client.get(f"/api/runs/{run_id}").json()["results"][1]["metrics"]["asr"] == 1.0
    record = client.get(f"/api/runs/{run_id}/records/gate_static_rules/user_task_0/injection_task_0").json()
    assert record["trace"][1]["judge"]["rule_ids"] == ["R2", "R3"]
    assert client.get(f"/api/runs/{run_id}/records/gate_static_rules/user_task_0/injection_task_9").status_code == 404
    trace = client.get(f"/api/runs/{run_id}/trace.json").json()
    assert len(trace["records"]) == 32 and trace["results"][0]["metrics"]["blocked_calls"] == 10
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/runs/..%2Fetc").status_code == 404


def test_llm_judge_reasons_give_rule_ids(client, monkeypatch):
    judge = {"judge": "llm", "verdict": "BLOCK", "text": "BLOCK. R3 destination not requested; R4 egress", "cached": True}
    row = dash.call_row(1, {"tool": "send_money", "args": {}, "effective": "BLOCK", "blocked": True, "judge": judge})
    assert row["rule_ids"] == ["R3", "R4"] and row["judge"]["kind"] == "llm" and row["judged"]
    plain = dash.call_row(2, {"tool": "get_balance", "args": {}, "effective": "ALLOW", "executed": True})
    assert not plain["judged"] and plain["rule_ids"] == [] and plain["reason"] is None


def test_a_crashed_run_reports_the_error(client, monkeypatch):
    def boom(jobs, workers):
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(dash.runner, "run_jobs", boom)
    done = wait_done(client, client.post("/api/runs", json=BODY).json()["run_id"])
    assert done["status"] == "failed" and "runner exploded" in done["error"]
    assert done["progress"]["done"] == 0
    assert all(r["state"] == "missing" for r in done["results"][0]["scenarios"])


# ---------------------------------------------------------------------------------------------- architecture
@pytest.mark.parametrize("framework", ["smolagents", "mini-swe-agent", "browser-use"])
def test_archify_diagrams_carry_the_agentgate_nodes(client, framework):
    response = client.get(f"/architecture/{framework}")
    assert response.status_code == 200
    html = response.text
    assert "Modified by agentgate" in html and "Copied from NiceGuy1313/" in html       # attribution kept
    nodes = set(re.findall(r'data-node-id="([^"]+)"', html))
    assert {"adapter", "gate", "agentdojo"} <= nodes
    edges = re.findall(r'<path data-edge-from="([^"]+)" data-edge-to="([^"]+)"[^>]*data-edge-key="(\d+)"', html)
    assert all(a in nodes and b in nodes for a, b, _ in edges)                        # no edge to a missing node
    keys = [k for _, _, k in edges]
    assert len(keys) == len(set(keys))                                                 # edge keys stay unique
    assert ("adapter", "gate") in {(a, b) for a, b, _ in edges}
    assert {("gate", "agentdojo")} <= {(a, b) for a, b, _ in edges}
    assert any(b == "gate" for _, b, _ in edges) and any(a == "gate" for a, _, _ in edges)
    assert len(html) > 100_000 and html == (ARCH / f"{framework}.html").read_text()


def test_native_has_no_diagram(client):
    assert client.get("/architecture/native").status_code == 404
    assert client.get("/architecture/nope").status_code == 404
