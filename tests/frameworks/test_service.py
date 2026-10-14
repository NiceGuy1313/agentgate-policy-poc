"""Wrapper contract v1 endpoints and the stdlib client (fail closed)."""

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
                                                             "condition": "C1"}}).json()
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
    assert attack["blocked"] and attack["policy_id"] and "blocked by security policy" in attack["output"]
    decision = client.post(f"/v1/sessions/{sid}/decide", headers=auth, json={
        "tool": "send_money", "args": {"recipient": "US133000000121212121212", "amount": 1,
                                       "subject": "x", "date": "2022-01-01"}}).json()
    assert decision["verdict"] in ("ESCALATE", "BLOCK")   # raw engine verdict (no escalate action applied)
    client.post(f"/v1/sessions/{sid}/events", headers=auth,
                json={"events": [{"kind": "llm_query", "message": "m", "step": 1}]})
    result = client.post(f"/v1/sessions/{sid}/finish", headers=auth, json={"final_output": "done"}).json()
    assert result == {"utility": False, "security": False, "security_executed": False}
    record = client.get(f"/v1/sessions/{sid}/record", headers=auth).json()
    assert [t["tool"] for t in record["trace"]] == ["read_file", "send_money"]
    assert client.post(f"/v1/sessions/{sid}/invoke", headers=auth, json={"tool": "read_file"}).status_code == 409


def test_client_fails_closed_when_gate_unreachable():
    gate = GateSession("http://127.0.0.1:9", "nope", "token", timeout=2)
    text = gate.invoke("send_money", {"amount": 1})
    assert text.startswith("Tool call blocked by security policy GATE")
    assert gate.decide("send_money", {})["verdict"] == "BLOCK"
