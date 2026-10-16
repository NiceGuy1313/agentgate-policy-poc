"""모든 툴 호출이 지나는 단일 게이트이다.

가로채는 지점은 `FunctionsRuntime` 서브클래스이다(docs/architecture.md 5절). AgentDojo `ToolsExecutor`(LLM 에이전트)와
`GroundTruthPipeline`(replay)은 모두 `runtime.run_function`으로만 툴을 실행하고, `TaskSuite.run_task_with_pipeline`은
`runtime_class`를 받으므로, `run_function`을 override하면 agentdojo 소스를 건드리지 않고 모든 실행 경로를 덮는다.
"""

from __future__ import annotations

import contextvars
import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, TaskEnvironment

from agentgate.config import load_policy_config
from agentgate.policy.base import PolicyContext, SessionState, ToolCall, Verdict
from agentgate.policy.engine import EngineResult, PolicyEngine
from agentgate.provenance import ValueIndex
from agentgate.task_spec import TaskSpec

INSIDE_GATE: contextvars.ContextVar[bool] = contextvars.ContextVar("agentgate_in_gate", default=False)


@dataclass
class TraceEvent:
    step: int
    tool: str
    args: dict
    raw_output: Any = None
    output_text: str = ""
    error: str | None = None
    blocked: bool = False
    executed: bool = False
    phase: str = ""                   # replay bookkeeping: "user" | "injection" | "agent"
    decision: dict | None = None      # serialized EngineResult
    decide_ms: float = 0.0
    detection: dict | None = None     # v2: detector label summary of this response
    toolspec: dict | None = None      # v3: ToolSpec summary of this response
    env_hash_before: str | None = None


EscalationJudgeFn = Callable[[ToolCall, EngineResult, PolicyContext], dict]
"""Returns {"verdict": "ALLOW"|"BLOCK", "human": bool, ...}."""


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, FunctionCall):
        return {"__call__": value.function, "args": _to_plain_data(dict(value.args))}
    if isinstance(value, Mapping):
        return {k: _to_plain_data(item) for k, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def compute_environment_hash(environment: TaskEnvironment | None) -> str | None:
    if environment is None:
        return None
    return hashlib.sha1(environment.model_dump_json().encode()).hexdigest()


def format_block_message(result: EngineResult) -> str:
    decision = result.primary_decision
    policy_id = decision.policy_id if decision else "POLICY"
    reason = decision.reason if decision else "The call was not allowed."
    return f"Tool call blocked by security policy {policy_id}: {reason}"


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
        judge: EscalationJudgeFn | None = None,
        record_env_hash: bool = False,
        detector=None,
        toolspec=None,
    ):
        super().__init__(functions)
        if mode not in ("audit", "enforce"):
            raise ValueError("mode must be audit | enforce")
        self.config = load_policy_config()
        self.engine = engine or PolicyEngine(self.config)
        self.mode = mode
        self.escalate_action = escalate_action or self.config["runtime"]["escalate_action"]
        self.judge = judge
        self.record_env_hash = record_env_hash
        self.detector = detector          # v2: labels every tool response; None keeps v1 behaviour exactly
        self.toolspec = toolspec          # v3: (tool, args, output_text) -> ToolSpec dict | None
        self.trace: list[TraceEvent] = []
        self.phase = ""
        self.session = SessionState()
        self.spec: TaskSpec | None = None
        self.index: ValueIndex | None = None
        self.spec_meta: dict = {}
        if prompt is not None and spec is not None:
            self.configure(prompt, spec)

    @classmethod
    def factory(cls, **kwargs) -> Callable[..., "GatedRuntime"]:
        """For `TaskSuite.run_task_with_pipeline(runtime_class=...)`, which calls `runtime_class(tools)`."""
        created: list[GatedRuntime] = []

        def make(functions=()):
            runtime = cls(functions, **kwargs)
            created.append(runtime)
            return runtime

        make.created = created  # type: ignore[attr-defined]
        return make

    def configure(self, prompt: str, spec: TaskSpec, meta: dict | None = None) -> None:
        self.spec = spec
        self.spec_meta = meta or {}
        engine_runtime = (getattr(self.engine, "config", None) or {}).get("runtime", {})
        self.index = ValueIndex(prompt, spec, self.config.get("self_identifiers"),
                                self.config["runtime"].get("min_string_field_len", 4),
                                engine_runtime.get("named_match_min_len", 4))

    @property
    def context(self) -> PolicyContext:
        return PolicyContext(self.spec, self.index, self.session, self.engine.config)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ the gate
    def run_function(self, env, function: str, kwargs, raise_on_error: bool = False):
        step = len(self.trace)
        args = _to_plain_data(dict(kwargs))
        event = TraceEvent(step=step, tool=function, args=args, phase=self.phase)
        if self.record_env_hash:
            event.env_hash_before = compute_environment_hash(env)

        start = time.perf_counter()
        if self.index is None:
            result = EngineResult(Verdict.BLOCK, [])
            event.decision = {"verdict": "BLOCK", "decisions": [], "note": "no TaskSpec configured (fail closed)"}
        else:
            result = self.engine.decide(ToolCall(function, args), self.context)
            event.decision = result.as_dict()
        effective = result.verdict
        if effective == Verdict.ESCALATE:
            if self.escalate_action == "allow":
                effective = Verdict.ALLOW
            elif self.escalate_action == "judge" and self.judge is not None:
                judgement = self.judge(ToolCall(function, args), result, self.context)
                # ④ (명세서.md 2절): an irreversible call stays with a human when its sources carry a detector
                # warning, even if the judge allowed it.
                if (judgement.get("final") == "ALLOW" and judgement.get("detector_warning")
                        and judgement.get("reversibility") == "irreversible"):
                    judgement = {**judgement, "final": "HUMAN", "verdict": "BLOCK", "human": True, "rule4": True}
                event.decision["judge"] = judgement
                effective = Verdict(judgement["verdict"])
            else:
                effective = Verdict.BLOCK
        event.decide_ms = (time.perf_counter() - start) * 1000
        event.decision["effective"] = effective.value

        if self.mode == "enforce" and effective == Verdict.BLOCK:
            event.blocked = True
            message = format_block_message(result)
            event.output_text = message
            self.trace.append(event)
            return (message, None) if raise_on_error else (message, message)

        token = INSIDE_GATE.set(True)
        try:
            output, error = super().run_function(env, function, kwargs, raise_on_error=raise_on_error)
        finally:
            INSIDE_GATE.reset(token)
        event.executed = True
        event.raw_output = output
        event.error = error
        try:
            event.output_text = tool_result_to_str(output) if error is None else ""
        except TypeError:
            event.output_text = str(output)
        if error is None and self.index is not None:
            source = self.index.add_response(step, function, args, output, event.output_text)
            if self.toolspec is not None:
                source.toolspec = self.toolspec(function, args, event.output_text)
                spec = source.toolspec or {}
                event.toolspec = {"ok": source.toolspec is not None, "records": len(spec.get("records", [])),
                                  "requests": len(spec.get("requests", [])), "named": source.is_named,
                                  "delegated": source.is_delegated}
            if self.detector is not None:
                detection = self.detector.detect(event.output_text)
                source.detection, source.detected_text = detection, event.output_text
                event.detection = {"detector": detection.detector, "mode": detection.mode,
                                   "score": round(detection.score, 4), "flagged": detection.flagged,
                                   "suspicious_spans": len(detection.suspicious_spans()),
                                   "latency_ms": round(detection.latency_ms, 1), "cached": detection.cached}
            self.session.record_call(function, args, output, source.sensitive_categories, source.source_id)
        self.trace.append(event)
        return output, error

    def _execute_nested_calls(self, env, kwargs):
        # nested FunctionCall arguments are executed through run_function, so they are gated too
        return super()._execute_nested_calls(env, kwargs)

    # ------------------------------------------------------------------ serialization
    def trace_records(self) -> list[dict]:
        records = []
        for event in self.trace:
            decision = event.decision or {}
            records.append({
                "step": event.step, "phase": event.phase, "tool": event.tool, "args": event.args,
                "verdict": decision.get("verdict"),
                "effective": decision.get("effective"),
                "decisions": decision.get("decisions", []),
                "judge": decision.get("judge"),
                "blocked": event.blocked, "executed": event.executed, "error": event.error,
                "decide_ms": round(event.decide_ms, 3),
                "output_excerpt": (event.output_text or "")[:300],
                "env_hash_before": event.env_hash_before,
                **({"detection": event.detection} if self.detector is not None else {}),
                **({"toolspec": event.toolspec} if self.toolspec is not None else {}),
            })
        return records
