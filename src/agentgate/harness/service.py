"""Wrapper contract v1 over HTTP: a gate session per scenario (docs/architecture.md, "Wrapper 연결 규약").

Mounted by the dashboard (`dashboard/app.py`); `serve_background()` starts it alone for batch runs of
contract Wrappers. Binds to 127.0.0.1 only; every session endpoint needs the session's bearer token.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agentgate.harness import CONTRACT_VERSION
from agentgate.harness.bridge import GateToolBridge
from agentgate.harness.events import EventSink
from agentgate.harness.session import ScenarioConfig, Session


@dataclass
class Entry:
    session: Session
    bridge: GateToolBridge
    sink: EventSink
    token: str
    framework: str = "external"
    model: dict = field(default_factory=dict)
    final_output: str | None = None
    finished: bool = False
    record: dict | None = None
    created: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.lock = threading.Lock()

    def add(self, session: Session, bridge: GateToolBridge, sink: EventSink, framework: str = "external",
            model: dict | None = None) -> tuple[str, str]:
        session_id, token = sink.run_id or str(uuid.uuid4()), secrets.token_urlsafe(24)
        sink.run_id = session_id
        with self.lock:
            self.entries[session_id] = Entry(session, bridge, sink, token, framework, model or {})
        return session_id, token

    def get(self, session_id: str, authorization: str | None) -> Entry:
        entry = self.entries.get(session_id)
        if entry is None:
            raise HTTPException(404, "unknown session")
        if not authorization or not secrets.compare_digest(authorization.removeprefix("Bearer ").strip(),
                                                           entry.token):
            raise HTTPException(401, "bad or missing session token")
        return entry


store = SessionStore()
router = APIRouter(prefix=f"/{CONTRACT_VERSION}")


class CreateSession(BaseModel):
    scenario: dict
    framework: str = "external"
    model: dict = {}


class ToolCallBody(BaseModel):
    tool: str
    args: dict = {}


class EventsBody(BaseModel):
    events: list[dict]


class FinishBody(BaseModel):
    final_output: str | None = None


@router.post("/sessions")
def create_session(body: CreateSession) -> dict:
    """For Wrappers that start on their own (e.g. a Wrapper's own dashboard)."""
    try:
        session = Session(ScenarioConfig(**body.scenario))
    except Exception as error:
        raise HTTPException(400, f"{type(error).__name__}: {error}") from None
    sink = EventSink(run_id=str(uuid.uuid4()))
    session_id, token = store.add(session, GateToolBridge(session, sink), sink, body.framework, body.model)
    sink.emit("run", f"Run started: {body.framework} · {session.config.suite}/{session.config.user_task}/"
                     f"{session.config.injection_task or '-'} · {session.config.condition}",
              data={"task": session.prompt, "scenario": session.config.as_dict(), "external": True})
    return {"session_id": session_id, "token": token, "prompt": session.prompt, "contract": CONTRACT_VERSION}


@router.get("/sessions/{session_id}")
def session_info(session_id: str, authorization: str | None = Header(None)) -> dict:
    entry = store.get(session_id, authorization)
    return {"session_id": session_id, "prompt": entry.session.prompt, "scenario": entry.session.config.as_dict(),
            "finished": entry.finished, "contract": CONTRACT_VERSION}


@router.get("/sessions/{session_id}/tools")
def session_tools(session_id: str, authorization: str | None = Header(None)) -> dict:
    return {"tools": store.get(session_id, authorization).bridge.tool_specs()}


@router.post("/sessions/{session_id}/invoke")
def invoke(session_id: str, body: ToolCallBody, authorization: str | None = Header(None)) -> dict:
    entry = store.get(session_id, authorization)
    if entry.finished:
        raise HTTPException(409, "session finished")
    output = entry.bridge.invoke(body.tool, body.args)
    last = entry.sink.events[-1].data if entry.sink.events else {}
    return {"output": output, "blocked": bool(last.get("blocked")), "verdict": last.get("verdict"),
            "effective": last.get("effective"), "policy_id": last.get("policy_id"), "reason": last.get("reason")}


@router.post("/sessions/{session_id}/decide")
def decide(session_id: str, body: ToolCallBody, authorization: str | None = Header(None)) -> dict:
    return store.get(session_id, authorization).bridge.decide(body.tool, body.args)


@router.post("/sessions/{session_id}/events")
def events(session_id: str, body: EventsBody, authorization: str | None = Header(None)) -> dict:
    entry = store.get(session_id, authorization)
    for event in body.events:
        if event.get("kind") in ("gate_decision", "result"):
            continue   # only agentgate itself reports gate decisions and results
        try:
            entry.sink.emit_raw(event)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    return {"accepted": len(body.events)}


@router.post("/sessions/{session_id}/finish")
def finish(session_id: str, body: FinishBody, authorization: str | None = Header(None)) -> dict:
    entry = store.get(session_id, authorization)
    entry.final_output = body.final_output
    result = entry.session.finish(body.final_output)
    if not entry.finished:
        entry.finished = True
        if entry.framework == "external":   # nobody else will build the record
            entry.record = entry.session.record(agent="framework", framework="external",
                                                messages=entry.bridge.messages(body.final_output),
                                                ungated_calls=[])
            entry.sink.emit("result", "Run finished", data={k: result.get(k) for k in
                                                            ("utility", "security", "security_executed",
                                                             "final_output")})
    return {k: result.get(k) for k in ("utility", "security", "security_executed")}


@router.get("/sessions/{session_id}/record")
def record(session_id: str, authorization: str | None = Header(None)) -> dict:
    entry = store.get(session_id, authorization)
    if entry.record is None:
        raise HTTPException(404, "no record yet")
    return entry.record


# ---------------------------------------------------------------------- standalone service for batch runs
_background: dict = {}


def service_url() -> str:
    """URL of a running contract service: the dashboard's, or a background one started on demand."""
    if "url" not in _background:
        import socket

        import uvicorn
        from fastapi import FastAPI

        app = FastAPI(title="agentgate contract service")
        app.include_router(router)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        _background.update(url=f"http://127.0.0.1:{port}", server=server)
    return _background["url"]


def set_service_url(url: str) -> None:
    """The dashboard serves the router itself; contract Wrappers it launches should call it there."""
    _background["url"] = url
