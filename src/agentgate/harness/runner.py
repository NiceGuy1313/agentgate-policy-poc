"""Run one (framework, model, scenario) and return the per-pair record. Shared by the dashboard and batch runs.

`run_scenario` runs in this process. `run_scenario_subprocess` runs the same function in a worker process
(`python -m agentgate.harness.worker`) with another interpreter, for frameworks whose dependencies cannot live
next to agentdojo; events stream back as JSON lines.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import traceback
import uuid
from dataclasses import dataclass, field

from agentgate.config import CONFIG_DIR, PROJECT_ROOT
from agentgate.harness import CONTRACT_VERSION, registry
from agentgate.harness.bridge import GateToolBridge
from agentgate.harness.events import EventSink, capture_loggers
from agentgate.harness.models import ModelSpec, model_from_dict
from agentgate.harness.session import ScenarioConfig, Session


@dataclass
class RunRequest:
    framework: str
    model: ModelSpec
    scenario: ScenarioConfig
    options: dict = field(default_factory=dict)
    max_steps: int | None = None

    def as_dict(self) -> dict:
        return {"framework": self.framework, "model": self.model.as_dict(), "scenario": self.scenario.as_dict(),
                "options": self.options, "max_steps": self.max_steps}

    @classmethod
    def from_dict(cls, data: dict) -> RunRequest:
        return cls(framework=data["framework"], model=model_from_dict(data["model"]),
                   scenario=ScenarioConfig(**data["scenario"]), options=data.get("options") or {},
                   max_steps=data.get("max_steps"))


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def config_hash() -> str:
    digest = hashlib.sha1()
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()[:12]


def manifest(request: RunRequest, adapter) -> dict:
    return {"framework": request.framework, "framework_version": adapter.version(),
            "contract_version": CONTRACT_VERSION, "model": request.model.as_dict(),
            "scenario": request.scenario.as_dict(), "options": request.options,
            "max_steps": request.max_steps or adapter.default_max_steps,
            "config_hash": config_hash(), "agentgate_commit": _git_commit(), "python": sys.version.split()[0]}


def run_scenario(request: RunRequest, sink: EventSink | None = None, session: Session | None = None) -> dict:
    sink = sink or EventSink(run_id=str(uuid.uuid4()))
    adapter = registry.get(request.framework)
    if reason := adapter.unavailable_reason():
        raise RuntimeError(f"framework {request.framework!r} unavailable: {reason}")
    if request.scenario.condition not in adapter.conditions:
        raise ValueError(f"{request.framework} does not support {request.scenario.condition}")
    max_steps = request.max_steps or adapter.default_max_steps
    run_manifest = manifest(request, adapter)
    session = session or Session(request.scenario)
    bridge = GateToolBridge(session, sink)
    sink.emit("run", f"Run started: {request.framework} · {request.model.label()} · "
                     f"{request.scenario.suite}/{request.scenario.user_task}/{request.scenario.injection_task or '-'}"
                     f" · {request.scenario.condition}",
              data={"manifest": run_manifest, "task": session.prompt,
                    "injection_goal": getattr(session.injection_task, "GOAL", None),
                    "injections": session.injections, "spec": session.runtime.spec.model_dump(),
                    "spec_source": session.spec_source})
    error = None
    try:
        with capture_loggers(sink, list(adapter.loggers)):
            result = adapter.run(session.prompt, bridge, request.model, request.options, max_steps, sink)
        error = result.error
    except Exception as exc:  # keep the record; the run counts as failed
        from agentgate.adapters.base import FrameworkResult
        result = FrameworkResult(final_output=None, error=f"{type(exc).__name__}: {exc}")
        error = f"{result.error}\n{traceback.format_exc()[-1500:]}"
    scored = session.finish(result.final_output)
    ungated = bridge.ungated_calls(set(adapter.internal_tools))
    record = session.record(
        agent="framework", framework=request.framework, agent_model=request.model.label(),
        manifest=run_manifest, error=error,
        messages=result.messages if result.messages is not None else bridge.messages(result.final_output),
        agent_tokens={**{"input": 0, "output": 0, "calls": 0, "refusals": 0}, **result.usage,
                      "llm_ms": round(1000 * sum(float(e.data.get("duration_seconds") or 0) for e in sink.events
                                                 if e.kind in ("llm_response", "llm_error")), 1),
                      "steps": result.steps},
        ungated_calls=ungated, framework_info=result.info)
    if ungated:
        record["valid"] = False
    sink.emit("result", "Run finished" if error is None else f"Run failed: {result.error}",
              level="ERROR" if error else "INFO",
              data={"utility": scored.get("utility"), "security": scored.get("security"),
                    "security_executed": scored.get("security_executed"), "final_output": scored.get("final_output"),
                    "blocked": sum(1 for e in record["trace"] if e["blocked"]), "tool_calls": len(record["trace"]),
                    "ungated_calls": ungated, "error": error, "usage": record["agent_tokens"]})
    return record


def run_scenario_subprocess(request: RunRequest, python: str, sink: EventSink | None = None) -> dict:
    """Same as run_scenario, in a worker process started with `python` (another venv)."""
    sink = sink or EventSink(run_id=str(uuid.uuid4()))
    process = subprocess.Popen([python, "-m", "agentgate.harness.worker"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT_ROOT)
    assert process.stdin and process.stdout
    process.stdin.write(json.dumps(request.as_dict()))
    process.stdin.close()
    record = None
    for line in process.stdout:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") == "event":
            event = message["event"]
            sink.emit(event["kind"], event.get("message", ""), level=event.get("level", "INFO"),
                      step=event.get("step"), data=event.get("data"))
        elif message.get("type") == "record":
            record = message["record"]
    stderr = process.stderr.read() if process.stderr else ""
    process.wait()
    if record is None:
        raise RuntimeError(f"worker failed (exit {process.returncode}): {stderr[-2000:]}")
    return record


def execute(request: RunRequest, sink: EventSink | None = None) -> dict:
    """Run in-process, or in a subprocess worker when config/frameworks.yaml gives the framework a `python`."""
    python = registry.entry(request.framework).get("python")
    if python:
        return run_scenario_subprocess(request, python, sink)
    return run_scenario(request, sink)
