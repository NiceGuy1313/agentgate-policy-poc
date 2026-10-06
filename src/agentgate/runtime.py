"""Tracking wrapper: the single gate every tool call passes through.

Interception point: a `FunctionsRuntime` subclass (docs/design_decisions.md §1). Both the
AgentDojo `ToolsExecutor` (LLM agents) and the `GroundTruthPipeline` (replay) execute tools
exclusively through `runtime.run_function`, and `TaskSuite.run_task_with_pipeline` accepts a
`runtime_class`, so overriding `run_function` covers every execution path without touching
agentdojo sources.
"""

from __future__ import annotations

import contextvars
import hashlib
import time
from collections.abc import Callable, Mapping
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, TaskEnvironment

from agentgate.config import policies_config
from agentgate.policy.base import Call, Context, Verdict
from agentgate.policy.engine import EngineResult, PolicyEngine
from agentgate.session import SessionState
from agentgate.spec import TaskSpec
from agentgate.trace import TraceEvent, ValueIndex

IN_GATE: contextvars.ContextVar[bool] = contextvars.ContextVar("agentgate_in_gate", default=False)

JudgeFn = Callable[[Call, EngineResult, Context], dict]
"""Returns {"verdict": "ALLOW"|"BLOCK", "human": bool, ...}."""


def _plain(v: Any) -> Any:
    if isinstance(v, FunctionCall):
        return {"__call__": v.function, "args": _plain(dict(v.args))}
    if isinstance(v, Mapping):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    return v


def env_hash(env: TaskEnvironment | None) -> str | None:
    if env is None:
        return None
    return hashlib.sha1(env.model_dump_json().encode()).hexdigest()


def block_message(result: EngineResult) -> str:
    d = result.primary
    pid = d.policy_id if d else "POLICY"
    reason = d.reason if d else "The call was not allowed."
    return f"Tool call blocked by security policy {pid}: {reason}"


class GatedRuntime(FunctionsRuntime):
    def __init__(
        self,
        functions=(),
        *,
        prompt: str | None = None,
        spec: TaskSpec | None = None,
        engine=None,
        mode: str = "enforce",
        escalate_action: str | None = None,
        judge: JudgeFn | None = None,
        record_env_hash: bool = False,
    ):
        super().__init__(functions)
        if mode not in ("audit", "enforce"):
            raise ValueError("mode must be audit | enforce")
        self.config = policies_config()
        self.engine = engine or PolicyEngine(self.config)
        self.mode = mode
        self.escalate_action = escalate_action or self.config["runtime"]["escalate_action"]
        self.judge = judge
        self.record_env_hash = record_env_hash
        self.trace: list[TraceEvent] = []
        self.phase = ""
        self.session = SessionState()
        self.spec: TaskSpec | None = None
        self.index: ValueIndex | None = None
        self.spec_meta: dict = {}
        if prompt is not None and spec is not None:
            self.configure(prompt, spec)

    @classmethod
    def factory(cls, **kw) -> Callable[..., "GatedRuntime"]:
        """For `TaskSuite.run_task_with_pipeline(runtime_class=...)`, which calls `runtime_class(tools)`."""
        created: list[GatedRuntime] = []

        def make(functions=()):
            rt = cls(functions, **kw)
            created.append(rt)
            return rt

        make.created = created  # type: ignore[attr-defined]
        return make

    def configure(self, prompt: str, spec: TaskSpec, meta: dict | None = None) -> None:
        self.spec = spec
        self.spec_meta = meta or {}
        self.index = ValueIndex(prompt, spec, self.config.get("self_identifiers"),
                                self.config["runtime"].get("min_string_field_len", 4))

    @property
    def context(self) -> Context:
        return Context(self.spec, self.index, self.session, self.engine.config)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ the gate
    def run_function(self, env, function: str, kwargs, raise_on_error: bool = False):
        step = len(self.trace)
        args = _plain(dict(kwargs))
        ev = TraceEvent(step=step, tool=function, args=args, phase=self.phase)
        if self.record_env_hash:
            ev.env_hash_before = env_hash(env)  # type: ignore[attr-defined]

        t0 = time.perf_counter()
        if self.index is None:
            result = EngineResult(Verdict.BLOCK, [])
            ev.decision = {"verdict": "BLOCK", "decisions": [], "note": "no TaskSpec configured (fail closed)"}
        else:
            result = self.engine.decide(Call(function, args), self.context)
            ev.decision = result.as_dict()
        effective = result.verdict
        if effective == Verdict.ESCALATE:
            if self.escalate_action == "allow":
                effective = Verdict.ALLOW
            elif self.escalate_action == "judge" and self.judge is not None:
                j = self.judge(Call(function, args), result, self.context)
                ev.decision["judge"] = j
                effective = Verdict(j["verdict"])
            else:
                effective = Verdict.BLOCK
        ev.decide_ms = (time.perf_counter() - t0) * 1000
        ev.decision["effective"] = effective.value

        if self.mode == "enforce" and effective == Verdict.BLOCK:
            ev.blocked = True
            msg = block_message(result)
            ev.output_text = msg
            self.trace.append(ev)
            return (msg, None) if raise_on_error else (msg, msg)

        token = IN_GATE.set(True)
        try:
            out, err = super().run_function(env, function, kwargs, raise_on_error=raise_on_error)
        finally:
            IN_GATE.reset(token)
        ev.executed = True
        ev.raw_output = out
        ev.error = err
        try:
            ev.output_text = tool_result_to_str(out) if err is None else ""
        except TypeError:
            ev.output_text = str(out)
        if err is None and self.index is not None:
            src = self.index.add_response(step, function, args, out, ev.output_text)
            self.session.record(function, args, out, src.sensitive, src.sid)
        self.trace.append(ev)
        return out, err

    def _execute_nested_calls(self, env, kwargs):
        # nested FunctionCall arguments are executed through run_function -> also gated
        return super()._execute_nested_calls(env, kwargs)

    # ------------------------------------------------------------------ serialization
    def trace_records(self) -> list[dict]:
        out = []
        for e in self.trace:
            out.append({
                "step": e.step, "phase": e.phase, "tool": e.tool, "args": e.args,
                "verdict": (e.decision or {}).get("verdict"),
                "effective": (e.decision or {}).get("effective"),
                "decisions": (e.decision or {}).get("decisions", []),
                "judge": (e.decision or {}).get("judge"),
                "blocked": e.blocked, "executed": e.executed, "error": e.error,
                "decide_ms": round(e.decide_ms, 3),
                "output_excerpt": (e.output_text or "")[:300],
                "env_hash_before": getattr(e, "env_hash_before", None),
            })
        return out
