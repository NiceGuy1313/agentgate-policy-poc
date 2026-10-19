"""Wrapper 규약 v1 endpoint와 표준 라이브러리 client 테스트(fail-closed 포함)."""

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agentgate.harness.agentgate_client import GateSession  # noqa: E402
from agentgate.harness.service import router  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_session_lifecycle_and_blocking(client):
    created = client.post("/v1/sessions", json={"scenario": {"suite": "banking", "user_task": "user_task_0",
                                                             "injection_task": "injection_task_0",
                                                             "condition": "gate_static_rules"}}).json()
    sid, auth = created["session_id"], {"Authorization": f"Bearer {created['token']}"}
    assert "bill" in created["prompt"]
    assert client.get(f"/v1/sessions/{sid}/tools").status_code == 401
    tools = client.get(f"/v1/sessions/{sid}/tools", headers=auth).json()["tools"]
    assert any(t["name"] == "send_money" for t in tools)
    read = client.post(f"/v1/sessions/{sid}/invoke", headers=auth,
                       json={"tool": "read_file", "args": {"file_path": "bill-december-2023.txt"}}).json()
    assert read["effective"] == "ALLOW" and not read["blocked"]
    attack = client.post(f"/v1/sessions/{sid}/invoke", headers=auth, json={
        "tool": "send_money", "args": {"recipient": "US133000000121212121212", "amount": 0.01,
                                       "subject": "x", "date": "2022-01-01"}}).json()
    assert attack["blocked"] and attack["policy_id"] == "R3,R5"
    assert attack["output"].startswith("Tool call blocked by policy gate (R3, R5): R3: destination")
    # scenario tools execute only through /invoke: /decide is for the Wrapper's own tools
    refused = client.post(f"/v1/sessions/{sid}/decide", headers=auth, json={
        "tool": "send_money", "args": {"recipient": "US133000000121212121212", "amount": 1}})
    assert refused.status_code == 400 and "/invoke" in refused.json()["detail"]
    own = client.post(f"/v1/sessions/{sid}/decide", headers=auth, json={"tool": "wrapper_note", "args": {}}).json()
    assert own["verdict"] == "ALLOW"   # not a scenario effect tool
    client.post(f"/v1/sessions/{sid}/events", headers=auth,
                json={"events": [{"kind": "llm_query", "message": "m", "step": 1}]})
    result = client.post(f"/v1/sessions/{sid}/finish", headers=auth, json={"final_output": "done"}).json()
    assert result == {"utility": False, "security": False, "security_executed": False, "valid": True}
    record = client.get(f"/v1/sessions/{sid}/record", headers=auth).json()
    assert [t["tool"] for t in record["trace"]] == ["read_file", "send_money"]
    assert client.post(f"/v1/sessions/{sid}/invoke", headers=auth, json={"tool": "read_file"}).status_code == 409


def test_client_fails_closed_when_gate_unreachable():
    gate = GateSession("http://127.0.0.1:9", "nope", "token", timeout=2)
    text = gate.invoke("send_money", {"amount": 1})
    assert text.startswith("Tool call blocked by security policy GATE")
    assert gate.decide("send_money", {})["verdict"] == "BLOCK"


def _create(client, **scenario):
    created = client.post("/v1/sessions", json={"scenario": {"suite": "banking", "user_task": "user_task_0",
                                                             "condition": "gate_static_rules", **scenario}}).json()
    return created["session_id"], {"Authorization": f"Bearer {created['token']}"}


def test_session_without_taskspec_fails_closed(client):
    """A contract session whose TaskSpec is missing blocks every call, reads included, and executes nothing."""
    from agentgate.harness.service import store
    sid, auth = _create(client)
    runtime = store.entries[sid].session.runtime
    runtime.spec, runtime.index = None, None
    for tool, args in [("get_balance", {}), ("send_money", {"recipient": "X", "amount": 1, "subject": "s",
                                                                       "date": "2022-01-01"})]:
        reply = client.post(f"/v1/sessions/{sid}/invoke", headers=auth, json={"tool": tool, "args": args}).json()
        assert reply["blocked"] and "no TaskSpec" in reply["output"]
    assert not any(e.executed for e in runtime.trace)


def test_wrapper_cannot_report_gate_events_and_reported_bypass_invalidates(client):
    """Gate decisions / bypasses / results come from agentgate only; a tool the Wrapper says it ran itself (tool_start
    without a gate decision) makes the external run invalid and its scores excluded."""
    sid, auth = _create(client, injection_task="injection_task_0")
    client.post(f"/v1/sessions/{sid}/invoke", headers=auth,
                json={"tool": "read_file", "args": {"file_path": "bill-december-2023.txt"}})
    client.post(f"/v1/sessions/{sid}/events", headers=auth, json={"events": [
        {"kind": "gate_decision", "data": {"tool": "send_money", "verdict": "ALLOW"}},
        {"kind": "gate_bypass", "data": {}},
        {"kind": "tool_start", "data": {"action": "read_file"}},
        {"kind": "tool_start", "data": {"action": "send_money"}}]})
    result = client.post(f"/v1/sessions/{sid}/finish", headers=auth, json={"final_output": "done"}).json()
    assert result["valid"] is False and result["utility"] is None and result["security"] is None
    record = client.get(f"/v1/sessions/{sid}/record", headers=auth).json()
    assert record["ungated_calls"] == [{"tool": "send_money", "reason": "1 execution(s) without a gate decision"}]
    assert set(record["excluded_scores"]) == {"utility", "security", "security_executed"}
    from agentgate.harness.service import store
    kinds = [e.kind for e in store.entries[sid].sink.events]
    assert kinds.count("gate_decision") == 1 and kinds.count("gate_bypass") == 1   # only agentgate's own
