"""agentgate 대시보드: 에이전트 프레임워크·모델·검증 방식을 골라 실행하고 결과를 본다.

시나리오 선택은 없다. `agentgate.scenarios.runner`의 고정 파일럿 세트(`PILOT`: banking, 사용자 작업 4 x injection task 3과
공격 없는 run 4개, 공격 `tool_knowledge`)를 고른 조건마다 `runner.run_jobs`로 background thread에서 돌린다. 페이지는
`GET /api/runs/{id}`로 진행(n/total)을 polling한 뒤 집계(`runner.summarize`: utility, ASR, 공격 하 utility, 차단 호출)와
시나리오별 표(행을 펼치면 호출별 Gate 판정)를 보여 준다.

    uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

실행마다 `dashboard/runs/<id>/`(git 제외)에 meta.json, scenario_summary.json, 시나리오별 레코드(runner와 같은 배치)를
쓴다. 실행 기록은 확인용이며 보고서 수치는 scripts/evaluation/live_evaluation.py 출력에서만 가져온다.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentgate.config import load_dotenv
from agentgate.experiment.conditions import CONDITIONS
from agentgate.harness import registry
from agentgate.harness.models import ModelSpec, model_from_dict
from agentgate.harness.service import router as contract_router
from agentgate.harness.service import set_service_url
from agentgate.harness.session import default_attack_model_name
from agentgate.scenarios import runner
from agentgate.scenarios.suites import load_suite, task_number

APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "runs"
NO_CACHE = {"Cache-Control": "no-cache"}
MAX_ACTIVE = 1          # a run sets AGENTGATE_AGENT_* for the process (runner.set_agent_model): one run at a time
WORKERS = 4             # concurrent scenarios inside a run (the CLI default)
PILOT_ATTACK = "tool_knowledge"
DEFAULT_SPEC = "llm"  # TaskSpec source: "llm" = Haiku extractor (official); "oracle" = hand-written AgentDojo specs (reference only)
MODEL_PRESETS = [{"provider": "ollama", "model": "gpt-oss:120b"},
                 {"provider": "ollama", "model": "mistral-large-3:675b"}]
# What the page calls each verification method (the condition names are the harness's).
CONDITION_UI = {
    "no_gate": {"label": "Gate 없음",
                "summary": "호출을 기록만 하고 모두 실행합니다. 기준선으로 씁니다."},
    "gate_llm_judge": {"label": "Policy 판단 LLM",
                       "summary": "effect 호출마다 판정 LLM이 룰북(R1–R10)을 읽고 ALLOW / BLOCK을 정합니다."},
    "gate_static_rules": {"label": "정규식·정적 규칙 비교",
                          "summary": "같은 룰북을 정적 코드(rule_checker)로 검사합니다. 판정에 LLM을 쓰지 않습니다."},
}
RULE_ID = re.compile(r"\bR(?:10|[1-9])\b")
RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
load_dotenv()


# ---------------------------------------------------------------------------------------------- the pilot set
@lru_cache(maxsize=1)
def pilot_pairs() -> list[tuple[str, str, str | None]]:
    """(suite, user task, injection task | None) of the pilot: the runner's PILOT, benign runs included."""
    return runner.select_pairs(runner.PILOT["suites"], runner.PILOT["user_tasks"], runner.PILOT["injection_tasks"],
                               benign=True)


@lru_cache(maxsize=1)
def pilot_info() -> dict:
    pairs = pilot_pairs()
    user_tasks, injection_tasks = [], []
    for suite_name in runner.PILOT["suites"]:
        suite = load_suite(suite_name)
        user_tasks += [{"suite": suite_name, "id": t, "prompt": suite.user_tasks[t].PROMPT}
                       for t in sorted(runner.PILOT["user_tasks"], key=task_number)]
        injection_tasks += [{"suite": suite_name, "id": t, "goal": suite.injection_tasks[t].GOAL}
                            for t in sorted(runner.PILOT["injection_tasks"], key=task_number)]
    return {"suites": runner.PILOT["suites"], "attack": PILOT_ATTACK, "total": len(pairs),
            "benign": sum(1 for _, _, i in pairs if i is None), "attacks": sum(1 for _, _, i in pairs if i),
            "user_tasks": user_tasks, "injection_tasks": injection_tasks}


# ---------------------------------------------------------------------------------------------- one scenario
def _rule_ids_in(text: str) -> list[str]:
    seen: list[str] = []
    for found in RULE_ID.findall(text or ""):
        if found not in seen:
            seen.append(found)
    return seen


def call_row(index: int, entry: dict) -> dict:
    """One tool call as the page shows it, from a trace record (record["trace"])."""
    judge = entry.get("judge") or None
    rule_ids = list((judge or {}).get("rule_ids") or [])
    reason = (judge or {}).get("reason") or (judge or {}).get("text")
    if judge and not rule_ids and judge.get("judge") != "rules":
        rule_ids = _rule_ids_in(reason or "")           # LLM judge: the ids it names in its answer
    if judge is None:
        policy = next((d for d in entry.get("decisions") or [] if d.get("verdict") == "BLOCK"), None)
        if policy:                                      # a policy engine decision (not the Policy Gate judges)
            rule_ids, reason = [policy["policy_id"]] if policy.get("policy_id") else [], policy.get("reason")
    return {"index": index, "tool": entry.get("tool"), "args": entry.get("args"),
            "verdict": entry.get("effective") or entry.get("verdict") or "?",
            "blocked": bool(entry.get("blocked")), "executed": bool(entry.get("executed")),
            "error": entry.get("error"), "judged": judge is not None,
            "judge": ({"kind": judge.get("judge"), "model": judge.get("model"), "cached": judge.get("cached"),
                       "latency_ms": judge.get("latency_ms"), "unparsed": judge.get("unparsed")}
                      if judge else None),
            "rule_ids": rule_ids, "reason": reason, "decide_ms": entry.get("decide_ms")}


def scenario_row(job: dict, record: dict | None, *, finished: bool) -> dict:
    """One row of the per-scenario table. `record` is the runner's record, or None while the scenario is pending."""
    row = {"condition": job["cond"], "suite": job["suite"], "user_task": job["user_task"],
           "injection_task": job["injection_task"], "attacked": job["injection_task"] is not None}
    if record is None:
        return {**row, "state": "missing" if finished else "pending"}
    calls = [call_row(i, entry) for i, entry in enumerate(record.get("trace") or [], 1)]
    valid = record.get("valid") is not False
    state = "invalid" if not valid else "error" if record.get("error") else "done"
    return {**row, "state": state, "utility": record.get("utility"), "security": record.get("security"),
            "security_executed": record.get("security_executed"),
            "blocked": sum(1 for c in calls if c["blocked"]), "tool_calls": len(calls),
            "executed": sum(1 for c in calls if c["executed"]), "calls": calls,
            "error": record.get("error"), "invalid_reason": record.get("invalid_reason"),
            "excluded_scores": record.get("excluded_scores"), "ungated_calls": record.get("ungated_calls") or [],
            "final_output": record.get("final_output")}


def _slim(record: dict) -> dict:
    """What `runner.summarize` reads of a record."""
    return {"condition": record.get("condition"), "suite": record.get("suite"), "user_task": record.get("user_task"),
            "injection_task": record.get("injection_task"), "valid": record.get("valid"),
            "invalid_reason": record.get("invalid_reason"), "error": record.get("error"), "utility": record.get("utility"), "security": record.get("security"),
            "security_executed": record.get("security_executed"),
            "trace": [{"blocked": e.get("blocked")} for e in record.get("trace") or []]}


def condition_info(name: str) -> dict:
    cond = CONDITIONS[name]
    ui = CONDITION_UI.get(name, {})
    return {"name": name, "label": ui.get("label", name), "summary": ui.get("summary", cond.description),
            "description": cond.description, "mode": cond.mode, "judge": cond.judge, "gated": cond.gated}


# ---------------------------------------------------------------------------------------------- run management
class Run:
    """One pilot run: the same scenario set under one or two conditions, executed by `runner.run_jobs`."""

    def __init__(self, plan: dict, run_id: str | None = None, started: float | None = None):
        self.id = run_id or str(uuid.uuid4())
        self.framework, self.model, self.conditions = plan["framework"], plan["model"], plan["conditions"]
        self.spec, self.options, self.max_steps = plan["spec"], plan["options"] or {}, plan["max_steps"]
        self.out = RUNS_DIR / self.id
        provider, _, model_name = self.model.partition(":")
        self.jobs = runner.build_jobs(
            self.conditions, pilot_pairs(), agent="llm", spec=self.spec, attack=PILOT_ATTACK,
            attack_model_name=default_attack_model_name(provider, model_name), on_block="continue",
            out=str(self.out), framework=self.framework, framework_options=json.dumps(self.options),
            model=self.model, max_steps=self.max_steps)
        self.status = "running"        # running | finished | failed (set last, after counts / error)
        self.error: str | None = None
        self.counts: dict | None = None  # run_jobs: status -> number of scenarios (ok, error, invalid, quota, ...)
        self.started = started or time.time()
        self.finished: float | None = None
        self._rows: dict[int, tuple[dict, dict]] = {}    # job index -> (table row, slim record), parsed once

    # -- persistence
    def meta(self) -> dict:
        return {"id": self.id, "framework": self.framework, "model": self.model, "conditions": self.conditions,
                "spec": self.spec, "options": self.options, "max_steps": self.max_steps, "status": self.status,
                "error": self.error, "counts": self.counts, "started": self.started, "finished": self.finished}

    @classmethod
    def from_meta(cls, meta: dict) -> Run:
        run = cls({k: meta.get(k) for k in ("framework", "model", "conditions", "spec", "options", "max_steps")},
                  run_id=meta["id"], started=meta.get("started"))
        run.status, run.error, run.counts, run.finished = (meta["status"], meta.get("error"), meta.get("counts"),
                                                           meta.get("finished"))
        return run

    # -- results
    def _scan(self) -> None:
        for index, job in enumerate(self.jobs):
            if index in self._rows:
                continue
            try:
                record = json.loads(runner.record_path(self.out, job).read_text())
            except (OSError, ValueError):      # not written yet, or being written
                continue
            self._rows[index] = (scenario_row(job, record, finished=True), _slim(record))

    def detail(self) -> dict:
        self._scan()
        finished = self.status != "running"
        rows = [self._rows[i][0] if i in self._rows else scenario_row(job, None, finished=finished)
                for i, job in enumerate(self.jobs)]
        summary = runner.summarize([slim for _, slim in self._rows.values()])["conditions"]
        results = []
        for name in self.conditions:
            mine = [r for r in rows if r["condition"] == name]
            scored = [r for r in mine if r["state"] == "done"]
            attacks = [r for r in scored if r["attacked"]]
            benign = [r for r in scored if not r["attacked"]]
            metrics = {"runs": 0, "scored_runs": 0, "errors": 0, "invalid_runs": 0, "benign_runs": 0,
                       "attack_runs": 0, "utility": None, "utility_under_attack": None, "asr": None,
                       "asr_executed_only": None, "tool_calls": 0, "blocked_calls": 0, "invalid": [],
                       **summary.get(name, {}),
                       "benign_success": sum(1 for r in benign if r["utility"]),
                       "attack_success": sum(1 for r in attacks if r["security"]),
                       "attack_success_executed": sum(1 for r in attacks if r["security_executed"]),
                       "attack_utility_success": sum(1 for r in attacks if r["utility"])}
            results.append({**condition_info(name), "metrics": metrics, "scenarios": mine,
                            "done": sum(1 for r in mine if r["state"] not in ("pending", "missing")),
                            "total": len(mine)})
        pilot = pilot_info()
        return {"id": self.id, "status": self.status, "started": self.started, "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - self.started, 1), "error": self.error,
                "counts": self.counts,
                "view": {"framework": self.framework, "model": self.model, "conditions": self.conditions,
                         "spec": self.spec, "suites": pilot["suites"], "attack": pilot["attack"]},
                "progress": {"done": sum(r["done"] for r in results), "total": len(self.jobs)},
                "results": results}

    def summary(self) -> dict:
        return {"id": self.id, "status": self.status, "started": self.started, "framework": self.framework,
                "model": self.model, "conditions": self.conditions}


runs: dict[str, Run] = {}
lock = threading.Lock()


def _execute(run: Run) -> None:
    try:
        runner.set_agent_model(run.model)       # the judge LLM's default model follows the agent (AGENTGATE_AGENT_*)
        runner.reset_quota_flag()               # a usage limit hit by an earlier run must not stop this one
        run.counts = runner.run_jobs(run.jobs, WORKERS)
        status = "finished"
    except Exception as error:
        run.error = f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"
        status = "failed"
    run.finished = time.time()
    try:   # saved before the status flips, so a client that sees "finished" can rely on the files
        run.out.mkdir(parents=True, exist_ok=True)
        runner.write_summary(run.out)
        (run.out / "meta.json").write_text(json.dumps({**run.meta(), "status": status}, ensure_ascii=False,
                                                      default=str, indent=1))
    except OSError:
        pass    # persistence is a convenience; the in-memory run is unaffected
    run.status = status


def _saved(run_id: str) -> Run | None:
    if not RUN_ID.fullmatch(run_id):
        return None
    try:
        return Run.from_meta(json.loads((RUNS_DIR / run_id / "meta.json").read_text()))
    except (OSError, ValueError, KeyError):
        return None


def _find(run_id: str) -> Run:
    run = runs.get(run_id) or _saved(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


# ---------------------------------------------------------------------------------------------- app
app = FastAPI(title="agentgate dashboard")
app.include_router(contract_router)


@app.middleware("http")
async def remember_base_url(request: Request, call_next):
    # contract Wrappers launched from here call the gate back on this server
    set_service_url(str(request.base_url).rstrip("/"))
    return await call_next(request)


# third-party browser assets, vendored so the dashboard works without a CDN (json-formatter-js, JetBrains Mono)
app.mount("/vendor", StaticFiles(directory=APP_DIR / "static" / "vendor"), name="vendor")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html", headers=NO_CACHE)


def architecture_path(framework: str) -> Path | None:
    """The framework's Archify diagram: the adapter's `architecture_html()`, else static/arch/<framework>.html."""
    try:
        path = registry.get(framework).architecture_html()
    except KeyError:
        return None
    path = path or APP_DIR / "static" / "arch" / f"{framework}.html"
    return path if path.is_file() else None


@app.get("/architecture/{framework}")
async def architecture(framework: str) -> FileResponse:
    """The framework's Archify diagram (static/arch/*.html) with the AgentGate Adapter, Policy Gate and AgentDojo
    tool nodes added at the tool-execution point. Archify's own toolbar, focus and zoom behaviour are unchanged."""
    path = architecture_path(framework)
    if path is None:
        raise HTTPException(404, "no architecture diagram for this framework")
    return FileResponse(path, headers=NO_CACHE)


@app.get("/api/catalog")
def catalog() -> dict:
    frameworks = [{**info, "has_architecture": architecture_path(info["name"]) is not None}
                  for info in registry.describe_all()]
    return {"frameworks": frameworks, "conditions": [condition_info(name) for name in CONDITIONS],
            "model_presets": MODEL_PRESETS, "pilot": pilot_info(), "default_spec": DEFAULT_SPEC,
            "max_active": MAX_ACTIVE}


@app.get("/api/models")
def models(provider: str = "ollama") -> dict:
    """Model names for the model field. Ollama: GET {host}/api/tags."""
    if provider != "ollama":
        return {"models": [p["model"] for p in MODEL_PRESETS if p["provider"] == provider]}
    spec = ModelSpec("ollama", "x")
    host = (spec.resolved_base_url() or "").removesuffix("/v1")
    try:
        data = requests.get(f"{host}/api/tags", timeout=5).json()
        return {"models": sorted(m["name"] for m in data.get("models", []))}
    except Exception as error:
        return {"models": [p["model"] for p in MODEL_PRESETS if p["provider"] == "ollama"],
                "error": f"{type(error).__name__}: {error}"}


@app.get("/api/status")
def status() -> dict:
    return {"missing_keys": {p: spec.missing_key() for p in ("anthropic", "openai", "ollama")
                             if (spec := ModelSpec(p, "x")).missing_key()},
            "active": [r.summary() for r in runs.values() if r.status == "running"]}


class RunBody(BaseModel):
    """The whole input of a run. The scenario set is fixed (the pilot), so there are no scenario fields."""
    model_config = ConfigDict(extra="forbid")

    framework: str
    model: str = Field(description="provider:model, e.g. ollama:gpt-oss:120b")
    condition: str
    compare_condition: str | None = Field(None, description="also run this condition on the same scenarios")
    spec: Literal["oracle", "llm"] = DEFAULT_SPEC     # TaskSpec source of the gated conditions
    max_steps: int | None = Field(None, ge=1, le=40)
    options: dict = Field(default_factory=dict)       # framework-specific options (options_schema)


def plan_run(body: RunBody) -> dict:
    """Validate a run body. HTTPException on a bad body; the result is what `Run` needs."""
    def invalid(message: str) -> HTTPException:
        return HTTPException(422, message)

    conditions = [body.condition, *([body.compare_condition] if body.compare_condition else [])]
    for name in conditions:
        if name not in CONDITIONS:
            raise invalid(f"알 수 없는 검증 방식입니다: {name} (사용 가능: {', '.join(CONDITIONS)})")
    if len(set(conditions)) != len(conditions):
        raise invalid("비교할 두 검증 방식이 같습니다.")
    if body.framework not in registry.names():
        raise invalid(f"알 수 없는 프레임워크입니다: {body.framework} (등록됨: {', '.join(registry.names())})")
    provider, _, model_name = body.model.partition(":")      # the model name may itself contain ':' (gpt-oss:120b)
    try:
        if not model_name:
            raise ValueError("model must be 'provider:model', e.g. ollama:gpt-oss:120b")
        model = model_from_dict({"provider": provider, "model": model_name})
    except ValueError as error:
        raise invalid(str(error)) from None
    adapter = registry.get(body.framework)
    if reason := adapter.unavailable_reason():
        raise HTTPException(400, f"{body.framework}를 현재 환경에서 쓸 수 없습니다: {reason}")
    if key := model.missing_key():
        raise HTTPException(400, f"{key}가 설정되지 않았습니다 (.env 또는 환경 변수, 서버를 다시 시작해야 합니다).")
    return {"framework": body.framework, "model": f"{model.provider}:{model.model}", "conditions": conditions,
            "spec": body.spec, "options": body.options, "max_steps": body.max_steps}


@app.post("/api/runs", status_code=202)
def start_run(body: RunBody) -> dict:
    """Start the pilot under one condition (two with `compare_condition`) in a background thread."""
    plan = plan_run(body)
    with lock:
        active = sum(1 for r in runs.values() if r.status == "running")
        if active >= MAX_ACTIVE:
            raise HTTPException(409, f"실행 중인 작업이 있습니다 (동시에 최대 {MAX_ACTIVE}건). 끝난 뒤 다시 실행하십시오.")
        run = Run(plan)
        runs[run.id] = run
        threading.Thread(target=_execute, args=(run,), daemon=True).start()
    return {"run_id": run.id}


@app.get("/api/runs")
def list_runs() -> dict:
    items = [r.summary() for r in runs.values()]
    return {"runs": sorted(items, key=lambda r: -r["started"])[:50]}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    """Progress (n/total) and, per condition, the aggregate metrics and the per-scenario rows with call verdicts."""
    return _find(run_id).detail()


@app.get("/api/runs/{run_id}/records/{condition}/{user_task}/{injection_task}")
def run_record(run_id: str, condition: str, user_task: str, injection_task: str) -> JSONResponse:
    """The runner's full record of one scenario (trace, messages, spec): debugging detail."""
    run = _find(run_id)
    job = next((j for j in run.jobs if (j["cond"], j["user_task"], j["injection_task"] or "none")
                == (condition, user_task, injection_task)), None)
    if job is None:
        raise HTTPException(404, "unknown scenario")
    try:
        record = json.loads(runner.record_path(run.out, job).read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "no record yet") from None
    return JSONResponse(json.loads(json.dumps(record, default=str)))


@app.get("/api/runs/{run_id}/trace.json")
def run_trace(run_id: str) -> JSONResponse:
    """Everything the run produced: the summary and every scenario record."""
    run = _find(run_id)
    records = []
    for job in run.jobs:
        try:
            records.append(json.loads(runner.record_path(run.out, job).read_text()))
        except (OSError, ValueError):
            continue
    body: dict[str, Any] = {**run.detail(), "records": records}
    return JSONResponse(json.loads(json.dumps(body, default=str)),
                        headers={"Content-Disposition": f'attachment; filename="agentgate-run-{run_id}.json"'})
