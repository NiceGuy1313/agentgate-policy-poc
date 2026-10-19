"""모든 툴 호출이 지나는 단일 게이트이다.

가로채는 지점은 `FunctionsRuntime` 서브클래스이다(docs/architecture.md). AgentDojo `ToolsExecutor`(LLM 에이전트)와
`GroundTruthPipeline`(replay)은 모두 `runtime.run_function`으로만 툴을 실행하고, `TaskSuite.run_task_with_pipeline`은
`runtime_class`를 받으므로, `run_function`을 override하면 agentdojo 소스를 건드리지 않고 모든 실행 경로를 덮는다.

판정은 `judge`(`agentgate.policy_gate.judge.Judge`: (call, context) -> 판정 기록)가 하고, `judge_scope`가 판정할 툴을
정한다. ToolSpec 부착 등은 서브클래스(`agentgate.policy_gate.runtime.PolicyGateRuntime`)가 `label_response`·`index_class`로
더한다. 런타임 설정은 config/gate.yaml이다.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, TaskEnvironment

from agentgate.config import load_config_yaml
from agentgate.core.provenance import ValueIndex
from agentgate.core.task_spec import TaskSpec
from agentgate.core.types import PolicyContext, SessionState, ToolCall, Verdict

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
    decision: dict | None = None      # {"verdict", "decisions", "judge", "effective"}: see run_function
    decide_ms: float = 0.0
    labels: dict = field(default_factory=dict)   # label summaries of this response (ToolSpec)
    env_hash_before: str | None = None


JudgeFn = Callable[[ToolCall, PolicyContext], dict]
"""Returns {"verdict": "ALLOW"|"BLOCK", ...} (the judge record stored in the trace); `agentgate.policy_gate.judge.Judge`."""


def to_plain_data(value: Any) -> Any:
    if isinstance(value, FunctionCall):
        return {"__call__": value.function, "args": to_plain_data(dict(value.args))}
    if isinstance(value, Mapping):
        return {k: to_plain_data(item) for k, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


_to_plain_data = to_plain_data   # former private name


def compute_environment_hash(environment: TaskEnvironment | None) -> str | None:
    if environment is None:
        return None
    return hashlib.sha1(environment.model_dump_json().encode()).hexdigest()


BLOCK_REASON_CHARS = 240
_RULE_ID = re.compile(r"\bR(?:10|[1-9])\b")


def format_judge_block_message(judgement: dict) -> str:
    """What the agent receives when the judge blocks: rule ids and a short one-line reason from the judge record,
    e.g. "Tool call blocked by policy gate (R2, R3): ...". The full judge record stays in the trace only."""
    reason = " ".join(str(judgement.get("reason") or judgement.get("text") or "").split())
    reason = re.sub(r"^[*#`_ ]*(?:BLOCK|ALLOW)\b[\s:.\-]*", "", reason, flags=re.IGNORECASE)   # LLM answer word
    rule_ids = list(judgement.get("rule_ids") or dict.fromkeys(_RULE_ID.findall(reason)))
    if len(reason) > BLOCK_REASON_CHARS:
        reason = reason[:BLOCK_REASON_CHARS - 1].rstrip() + "…"
    label = f" ({', '.join(rule_ids)})" if rule_ids else ""
    return f"Tool call blocked by policy gate{label}: {reason or 'the call is outside the requested task'}"


NO_TASKSPEC_MESSAGE = "Tool call blocked by policy gate: no TaskSpec is configured for this task (fail closed)."


class UngatedExecutionError(PermissionError):
    """A tool function was called outside `GatedRuntime.run_function` (a gate bypass); it was not executed."""


def _guarded(function, ungated: list[dict]):
    """Copy of an AgentDojo `Function` that runs only inside the gate; any other call is refused and recorded."""
    run = function.run

    def guarded_run(*args, **kwargs):
        if not INSIDE_GATE.get():
            ungated.append({"tool": function.name, "reason": "executed outside the policy gate (refused)"})
            raise UngatedExecutionError(f"{function.name} may only run through the policy gate")
        return run(*args, **kwargs)

    return function.model_copy(update={"run": guarded_run})


class GatedRuntime(FunctionsRuntime):
    index_class = ValueIndex
    trace_label_keys: tuple[str, ...] = ()   # label summaries copied into trace_records (subclasses)

    def __init__(
        self,
        functions=(),
        *,
        prompt: str | None = None,
        spec: TaskSpec | None = None,
        mode: str = "enforce",
        judge: JudgeFn | None = None,
        judge_scope: Callable[[str], bool] | None = None,
        record_env_hash: bool = False,
        config: dict | None = None,
    ):
        super().__init__(functions)
        # every tool function refuses to run outside run_function (bypass guard); refusals are listed here
        self.ungated_executions: list[dict] = []
        self.functions = {name: _guarded(f, self.ungated_executions) for name, f in self.functions.items()}
        if mode not in ("audit", "enforce"):
            raise ValueError("mode must be audit | enforce")
        # runtime settings (self identifiers, index lengths): explicit `config` or config/gate.yaml
        self.config = config if config is not None else load_config_yaml("gate.yaml")
        self.mode = mode
        self.judge = judge
        self.judge_scope = judge_scope   # tool name -> judged? (None: every tool)
        self.record_env_hash = record_env_hash
        self.trace: list[TraceEvent] = []
        self.phase = ""
        self.session = SessionState()
        self.prompt = ""
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
        self.prompt = prompt
        self.spec = spec
        self.spec_meta = meta or {}
        self.index = self.index_class(prompt, spec, self.config.get("self_identifiers"),
                                      self.config["runtime"].get("min_string_field_len", 4),
                                      self.config["runtime"].get("named_match_min_len", 4))

    @property
    def context(self) -> PolicyContext:
        return PolicyContext(self.spec, self.index, self.session, self.config, self.prompt)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ the gate
    def run_function(self, env, function: str, kwargs, raise_on_error: bool = False):
        step = len(self.trace)
        args = to_plain_data(dict(kwargs))
        event = TraceEvent(step=step, tool=function, args=args, phase=self.phase)
        if self.record_env_hash:
            event.env_hash_before = compute_environment_hash(env)

        start = time.perf_counter()
        # trace schema: "verdict" is BLOCK only when fail-closed, else ALLOW; the judge's answer is "judge"/"effective"
        if self.index is None:
            event.decision = {"verdict": "BLOCK", "decisions": [], "note": "no TaskSpec configured (fail closed)"}
            effective, judgement = Verdict.BLOCK, None
        else:
            event.decision = {"verdict": "ALLOW", "decisions": []}
            effective, judgement = self.resolve(ToolCall(function, args))
        if judgement is not None:
            event.decision["judge"] = judgement
        event.decide_ms = (time.perf_counter() - start) * 1000
        event.decision["effective"] = effective.value

        if self.mode == "enforce" and effective == Verdict.BLOCK:
            event.blocked = True
            message = self.block_message(judgement)
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
            self.label_response(source, event)
            self.session.record_call(function, output)
        self.trace.append(event)
        return output, error

    # ------------------------------------------------------------------ judgement hooks
    def resolve(self, call: ToolCall) -> tuple[Verdict, dict | None]:
        """Effective verdict and judge record of a call (the TaskSpec is configured). A tool outside `judge_scope` is
        allowed unjudged. Without a judge the call is allowed, except that an enforcing runtime with a judge scope (the
        policy gate) refuses to run: it would allow every call it is meant to judge."""
        if self.judge_scope is not None and not self.judge_scope(call.tool):
            return Verdict.ALLOW, None
        if self.judge is None:
            if self.mode == "enforce" and self.judge_scope is not None:
                raise RuntimeError("policy gate in enforce mode needs a judge (judge None)")
            return Verdict.ALLOW, None
        judgement = self.judge(call, self.context)
        return Verdict(judgement["verdict"]), judgement

    def block_message(self, judgement: dict | None) -> str:
        """Text the agent receives for a blocked call: the judge's rule ids and reason."""
        if self.index is None:
            return NO_TASKSPEC_MESSAGE
        if judgement is not None:
            return format_judge_block_message(judgement)
        return "Tool call blocked by security policy POLICY: The call was not allowed."

    def label_response(self, source, event: TraceEvent) -> None:
        """Attach labels to an executed response (PolicyGateRuntime: ToolSpec). Default: none."""

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
                **{key: event.labels.get(key) for key in self.trace_label_keys},
            })
        return records
