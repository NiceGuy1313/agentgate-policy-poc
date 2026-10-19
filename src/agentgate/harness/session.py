"""Gate 뒤의 시나리오 하나(`Session`): 환경(`agentgate.environments`의 `ScenarioRun`: 툴, 인젝션, 채점) + PolicyGateRuntime + TaskSpec.

모든 에이전트 프레임워크는 `GateToolBridge`를 거쳐 `Session`과 통신한다. 단계는
`agentgate.scenarios.pipeline.run_live_task`(conformance 기준 경로)와 같지만 에이전트 루프는 어댑터에 맡긴다.
프레임워크는 `invoke`로 툴을 부르고 `finish`로 최종 답을 넘긴다. 시나리오 CLI·대시보드는 native를 포함한 모든
프레임워크를 이 Session으로 돌린다(`agentgate.experiment.batch.run_framework_one`).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import InitVar, dataclass

from agentgate.environments import get_environment
from agentgate.experiment.conditions import CONDITIONS, get_condition, make_runtime

FRAMEWORK_CONDITIONS = tuple(CONDITIONS)   # every framework runs under every condition
SCORE_KEYS = ("utility", "security", "security_executed")


def invalidate_if_ungated(record: dict, ungated: list[dict]) -> dict:
    """A run with any tool execution that bypassed the policy gate is invalid: `valid` is False, the scores move to
    `excluded_scores` and are None in the record, so no summary counts them."""
    record["ungated_calls"] = ungated
    record["valid"] = not ungated
    if ungated:
        record["invalid_reason"] = ("ungated tool execution (policy gate bypassed): "
                                    + ", ".join(sorted({u.get("tool") or "?" for u in ungated})))
        record["excluded_scores"] = {key: record.get(key) for key in SCORE_KEYS}
        for key in SCORE_KEYS:
            record[key] = None
    return record


def default_attack_model_name(provider: str, model: str) -> str:
    """Name filled into the attack template; same rule as live_evaluation.py."""
    return {"anthropic": "Claude", "openai": "GPT-4"}.get(provider, re.split(r"[-:_]", model)[0].upper()
                                                          or "AI assistant")


@dataclass
class ScenarioConfig:
    suite: str
    user_task: str
    injection_task: str | None = None
    condition: str = "gate_static_rules"
    taskspec_source: str = "oracle"            # TaskSpec source for gated conditions: oracle | llm
    attack: str = "important_instructions"
    attack_model_name: str = "AI assistant"
    spec: InitVar[str | None] = None           # former name of taskspec_source (HTTP / worker payloads still use it)

    def __post_init__(self, spec: str | None):
        if spec is not None:
            self.taskspec_source = spec
        if self.condition not in CONDITIONS:
            raise ValueError(f"unknown condition {self.condition!r}")
        if self.taskspec_source not in ("oracle", "llm"):
            raise ValueError("spec must be oracle | llm")
        if self.injection_task in ("", "none"):
            self.injection_task = None

    def as_dict(self) -> dict:
        """The contract v1 payload: the TaskSpec source keeps its key "spec"."""
        return {"suite": self.suite, "user_task": self.user_task, "injection_task": self.injection_task,
                "condition": self.condition, "spec": self.taskspec_source, "attack": self.attack,
                "attack_model_name": self.attack_model_name}


# read access under the former name (the InitVar default left a class attribute; replace it with an alias)
ScenarioConfig.spec = property(lambda self: self.taskspec_source)   # type: ignore[assignment]


class Session:
    def __init__(self, config: ScenarioConfig, environment: str = "agentdojo"):
        self.config = config
        self.environment = get_environment(environment)
        self.scenario = self.environment.open(config)
        # AgentDojo objects kept as attributes (scripted plans and existing callers read them)
        self.suite = getattr(self.scenario, "suite", None)
        self.user_task = getattr(self.scenario, "user_task", None)
        self.injection_task = getattr(self.scenario, "injection_task", None)
        self.injections: dict[str, str] = self.scenario.injections
        self.pre_env = getattr(self.scenario, "pre_env", None)

        cond = get_condition(config.condition)
        self.stats: dict = {}
        self.runtime = make_runtime(cond, self.scenario.functions, suite_name=config.suite, prompt=self.prompt,
                                    stats=self.stats, record_env_hash=True, binding=self.environment.binding)
        self.spec_source = config.taskspec_source if cond.gated else "oracle(audit)"
        provider = self.environment.spec_provider(config.taskspec_source if cond.gated else "oracle", config.suite,
                                                  config.user_task)
        started = time.perf_counter()
        spec, meta = provider(self.prompt)
        self.runtime.configure(self.prompt, spec, {**meta, "wall_ms": (time.perf_counter() - started) * 1000})
        self.lock = threading.Lock()
        self.result: dict | None = None
        self.started = time.perf_counter()

    @property
    def env(self):
        """The live environment the tools mutate (the scenario's; the native adapter reassigns it)."""
        return self.scenario.env

    @env.setter
    def env(self, value) -> None:
        self.scenario.env = value

    # ------------------------------------------------------------------ what the agent sees
    @property
    def prompt(self) -> str:
        return self.scenario.prompt

    def tool_schemas(self) -> list[dict]:
        return self.scenario.tool_schemas()

    tool_specs = tool_schemas   # former name

    def invoke(self, tool: str, args: dict) -> tuple[str, dict]:
        """Run one tool call through the gate. Returns (text the agent receives, gate trace record)."""
        with self.lock:
            if self.result is not None:
                raise RuntimeError("session already finished")
            output, error = self.runtime.run_function(self.env, tool, dict(args or {}))
            event = self.runtime.trace[-1] if self.runtime.trace and self.runtime.trace[-1].tool == tool else None
            record = self.runtime.trace_records()[-1] if event is not None else {}
            text = error if error is not None else (event.output_text if event is not None else str(output))
            return text, record

    def decide(self, tool: str, args: dict) -> dict:
        """Gate 판정만 한다(실행·상태 변경 없음). Wrapper 자체 툴용이다. TaskSpec이 없으면 BLOCK한다(fail-closed)."""
        from agentgate.core.types import ToolCall
        with self.lock:
            if self.runtime.index is None:   # `GatedRuntime.run_function`과 같다: TaskSpec 없이는 아무것도 허용하지 않는다
                return {"verdict": "BLOCK", "decisions": [], "effective": "BLOCK", "judge": None,
                        "note": "no TaskSpec configured (fail closed)"}
            effective, judgement = self.runtime.resolve(ToolCall(tool, dict(args or {})))
            return {"verdict": effective.value, "decisions": [], "effective": effective.value, "judge": judgement}

    # ------------------------------------------------------------------ end of run
    def finish(self, final_output: str | None) -> dict:
        with self.lock:
            if self.result is not None:
                return self.result
            result = self.scenario.score(final_output, self.runtime)
            result["wall_ms"] = (time.perf_counter() - self.started) * 1000
            result["final_output"] = (final_output or "")[:2000]
            self.result = result
            return result

    def ungated_executions(self) -> list[dict]:
        """Tool functions called outside the gate during this session (refused by the runtime)."""
        return list(self.runtime.ungated_executions)

    def record(self, **extra) -> dict:
        """Per-pair record in the live_evaluation.py schema (check_enforcement / summarize_live read it as is).

        `ungated_calls` (bridge audit) and the runtime's refused executions decide `valid` (`invalidate_if_ungated`)."""
        result = self.result or {}
        ungated = list(extra.pop("ungated_calls", None) or []) + self.ungated_executions()
        record = {
            "condition": self.config.condition, "suite": self.config.suite, "user_task": self.config.user_task,
            "injection_task": self.config.injection_task,
            "attack": self.config.attack if self.injection_task else None,
            "spec_source": self.spec_source, "spec_meta": self.runtime.spec_meta,
            "spec": self.runtime.spec.model_dump() if self.runtime.spec is not None else None,
            **result,
            "gate_ms": round(sum(e.decide_ms for e in self.runtime.trace), 3),
            "judge": self.runtime.judge_stats() if get_condition(self.config.condition).judge else None,
            "toolspec": self.stats.get("toolspec"),
            "trace": self.runtime.trace_records(),
            **extra,
        }
        return invalidate_if_ungated(record, ungated)
