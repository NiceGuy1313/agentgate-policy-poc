"""agentgate dashboard: pick the test variables, run an agent framework on an AgentDojo scenario behind the gate,
and watch LLM calls, tool calls and gate decisions live.

Adapted from the "Agent Observatory" dashboards of the NiceGuy1313 forks (smolagents: Apache-2.0,
mini-swe-agent: MIT, browser-use: MIT): same event stream (SSE) and call-history layout, extended with gate
decisions, scenario selection, side-by-side comparison and the Wrapper contract v1 endpoints (/v1/...).

    uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

Runs are saved to dashboard/runs/ (git-ignored). They are for inspection only; report numbers come from
scripts/evaluation/live_evaluation.py.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentgate import SUITES
from agentgate.config import load_dotenv
from agentgate.harness import registry
from agentgate.harness.events import EventSink
from agentgate.harness.models import PROVIDERS, ModelSpec, model_from_dict
from agentgate.harness.runner import RunRequest, execute, run_scenario
from agentgate.harness.scenario import ScenarioConfig, Session, default_attack_model_name
from agentgate.harness.service import router as contract_router
from agentgate.harness.service import set_service_url, store
from agentgate.suites import load_suite, task_number

APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "runs"
NO_CACHE = {"Cache-Control": "no-cache"}
MAX_ACTIVE = 2
MODEL_PRESETS = [{"provider": "ollama", "model": "gpt-oss:120b"},
                 {"provider": "ollama", "model": "mistral-large-3:675b"}]
ATTACKS = ["important_instructions", "tool_knowledge", "ignore_previous", "system_message", "direct", "injecagent"]
load_dotenv()


class Run:
    def __init__(self, request: RunRequest, label: str):
        self.id = str(uuid.uuid4())
        self.request, self.label = request, label
        self.sink = EventSink(run_id=self.id)
        self.status = "running"
        self.record: dict | None = None
        self.session = None
        self.started = time.time()

    def summary(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status, "started": self.started,
                "request": self.request.as_dict(),
                "result": {k: (self.record or {}).get(k) for k in ("utility", "security", "security_executed")}}


runs: dict[str, Run] = {}
lock = threading.Lock()


def _execute(run: Run) -> None:
    try:
        if registry.entry(run.request.framework).get("python"):
            run.record = execute(run.request, run.sink)       # subprocess worker
        else:
            run.session = Session(run.request.scenario)
            run.record = run_scenario(run.request, run.sink, run.session)
        run.status = "failed" if run.record.get("error") else "finished"
    except Exception as error:
        run.status = "failed"
        run.sink.emit("result", f"Run failed: {type(error).__name__}: {error}", level="ERROR",
                      data={"error": traceback.format_exc()[-2000:]})
    RUNS_DIR.mkdir(exist_ok=True)
    (RUNS_DIR / f"{run.id}.json").write_text(json.dumps(
        {**run.summary(), "record": run.record, "events": [e.as_dict() for e in run.sink.events]},
        ensure_ascii=False, default=str, indent=1))


app = FastAPI(title="agentgate dashboard")
app.include_router(contract_router)


@app.middleware("http")
async def remember_base_url(request: Request, call_next):
    # contract Wrappers launched from here call the gate back on this server
    set_service_url(str(request.base_url).rstrip("/"))
    return await call_next(request)


# third-party browser libraries, vendored so the dashboard works without a CDN
app.mount("/vendor", StaticFiles(directory=APP_DIR / "static" / "vendor"), name="vendor")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html", headers=NO_CACHE)


@app.get("/architecture/{framework}")
async def architecture(framework: str) -> FileResponse:
    try:
        path = registry.get(framework).architecture_html()
    except KeyError:
        path = None
    if path is None:
        raise HTTPException(404, "no architecture diagram for this framework")
    return FileResponse(path, headers=NO_CACHE)


@app.get("/api/catalog")
def catalog() -> dict:
    suites = {}
    for name in SUITES:
        suite = load_suite(name)
        suites[name] = {
            "user_tasks": [{"id": t, "prompt": suite.user_tasks[t].PROMPT}
                           for t in sorted(suite.user_tasks, key=task_number)],
            "injection_tasks": [{"id": t, "goal": suite.injection_tasks[t].GOAL}
                                for t in sorted(suite.injection_tasks, key=task_number)]}
    return {"frameworks": registry.describe_all(), "suites": suites, "attacks": ATTACKS,
            "conditions": {"C0": "방어 없음 (audit, 판정만 기록)", "C1": "Gate enforce · ESCALATE→차단",
                           "C2": "Gate enforce · ESCALATE→허용", "C3": "AgentDojo spotlighting (native만)",
                           "C4": "Gate enforce · ESCALATE→판정 LLM"},
            "providers": list(PROVIDERS), "model_presets": MODEL_PRESETS, "max_active": MAX_ACTIVE}


@app.get("/api/models")
def models(provider: str = "ollama") -> dict:
    """Model names for the model field. Ollama: GET {host}/api/tags."""
    if provider != "ollama":
        return {"models": [p["model"] for p in MODEL_PRESETS if p["provider"] == provider]}
    spec = ModelSpec("ollama", "x")
    host = (spec.resolved_base_url() or "").removesuffix("/v1")
    try:
        data = requests.get(f"{host}/api/tags", timeout=10).json()
        return {"models": sorted(m["name"] for m in data.get("models", []))}
    except Exception as error:
        return {"models": [p["model"] for p in MODEL_PRESETS if p["provider"] == "ollama"],
                "error": f"{type(error).__name__}: {error}"}


@app.get("/api/status")
def status() -> dict:
    return {"missing_keys": {p: ModelSpec(p, "x").missing_key() for p in PROVIDERS
                             if p != "openai_compat" and ModelSpec(p, "x").missing_key()},
            "active": [r.summary() for r in runs.values() if r.status == "running"]}


@app.get("/api/scenario")
def scenario(suite: str, user_task: str, injection_task: str | None = None,
             attack: str = "important_instructions", attack_model_name: str = "AI assistant") -> dict:
    """Preview: user prompt, injection GOAL and the injected texts, oracle TaskSpec."""
    from agentgate.evaluation.replay import REPLAY_PIPELINE_NAME, get_cached_attack
    from agentgate.task_spec import load_task_spec
    suite_obj = load_suite(suite)
    task = suite_obj.get_user_task_by_id(user_task)
    out: dict[str, Any] = {"prompt": task.PROMPT}
    try:
        out["oracle_spec"] = load_task_spec("oracle", suite, user_task).model_dump()
    except Exception:
        out["oracle_spec"] = None
    if injection_task and injection_task != "none":
        injection = suite_obj.get_injection_task_by_id(injection_task)
        out["goal"] = injection.GOAL
        attack_obj = get_cached_attack(suite, attack, REPLAY_PIPELINE_NAME)
        attack_obj.model_name = attack_model_name
        out["injections"] = attack_obj.attack(task, injection)
    return out


class StartRuns(BaseModel):
    runs: list[dict] = Field(min_length=1, max_length=MAX_ACTIVE)


@app.post("/api/runs", status_code=202)
def start_runs(body: StartRuns) -> dict:
    requests_: list[tuple[RunRequest, str]] = []
    for item in body.runs:
        try:
            model = model_from_dict(item["model"])
            scenario_data = dict(item["scenario"])
            scenario_data.setdefault("attack_model_name", default_attack_model_name(model.provider, model.model))
            request = RunRequest(item["framework"], model, ScenarioConfig(**scenario_data),
                                 item.get("options") or {}, item.get("max_steps"))
            adapter = registry.get(request.framework)
        except Exception as error:
            raise HTTPException(400, f"{type(error).__name__}: {error}") from None
        if reason := adapter.unavailable_reason():
            raise HTTPException(400, f"{request.framework}: {reason}")
        if request.scenario.condition not in adapter.conditions:
            raise HTTPException(400, f"{request.framework} does not support {request.scenario.condition}")
        if key := model.missing_key():
            raise HTTPException(400, f"{key} is not set (.env or environment)")
        if request.max_steps is not None and not 1 <= request.max_steps <= 40:
            raise HTTPException(400, "max_steps must be 1..40")
        requests_.append((request, item.get("label") or ""))
    with lock:
        active = sum(1 for r in runs.values() if r.status == "running")
        if active + len(requests_) > MAX_ACTIVE:
            raise HTTPException(409, f"{active} run(s) already running (limit {MAX_ACTIVE})")
        started = []
        for request, label in requests_:
            run = Run(request, label)
            runs[run.id] = run
            threading.Thread(target=_execute, args=(run,), daemon=True).start()
            started.append(run.id)
    return {"run_ids": started}


@app.get("/api/runs")
def list_runs() -> dict:
    items = [r.summary() for r in runs.values()]
    items += [{"id": sid, "label": f"external · {e.framework}", "status": "finished" if e.finished else "running",
               "started": e.created, "request": {"scenario": e.session.config.as_dict(), "framework": e.framework},
               "external": True}
              for sid, e in store.entries.items() if sid not in runs]
    return {"runs": sorted(items, key=lambda r: -r["started"])[:50]}


def _sink(run_id: str) -> EventSink:
    if run_id in runs:
        return runs[run_id].sink
    if run_id in store.entries:
        return store.entries[run_id].sink
    raise HTTPException(404, "unknown run")


@app.get("/api/runs/{run_id}/trace.json")
def run_trace(run_id: str) -> JSONResponse:
    sink = _sink(run_id)
    run = runs.get(run_id)
    body = {**(run.summary() if run else {"id": run_id}), "record": run.record if run else None,
            "events": [e.as_dict() for e in sink.events]}
    return JSONResponse(json.loads(json.dumps(body, default=str)),
                        headers={"Content-Disposition": f'attachment; filename="agentgate-run-{run_id}.json"'})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """SSE: the run's history so far, then live events."""
    sink = _sink(run_id)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    def enqueue(event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def stream():
        backlog = sink.subscribe(enqueue)
        try:
            for event in backlog:
                yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False, default=str)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False, default=str)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            sink.unsubscribe(enqueue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
