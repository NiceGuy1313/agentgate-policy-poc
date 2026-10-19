"""Gate 뒤의 AgentDojo 시나리오 하나(`Session`): 환경 + 인젝션 + PolicyGateRuntime + TaskSpec + 채점.

모든 에이전트 프레임워크는 `GateToolBridge`를 거쳐 `Session`과 통신한다. 단계는
`agentgate.scenarios.pipeline.run_live_task`(conformance 기준 경로)와 같지만 에이전트 루프는 어댑터에 맡긴다.
프레임워크는 `invoke`로 툴을 부르고 `finish`로 최종 답을 넘긴다. 시나리오 CLI·대시보드는 native를 포함한 모든
프레임워크를 이 Session으로 돌린다(`agentgate.scenarios.runner.run_framework_one`).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass

from agentdojo.functions_runtime import FunctionCall
from agentdojo.types import text_content_block_from_string

from agentgate.agents.factory import spec_provider
from agentgate.experiment.conditions import CONDITIONS, get_condition, make_runtime
from agentgate.scenarios.pipeline import score_task
from agentgate.scenarios.replay import REPLAY_PIPELINE_NAME, get_cached_attack
from agentgate.scenarios.suites import load_suite

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
    spec: str = "oracle"                       # TaskSpec source for gated conditions: oracle | llm
    attack: str = "important_instructions"
    attack_model_name: str = "AI assistant"

    def __post_init__(self):
        if self.condition not in CONDITIONS:
            raise ValueError(f"unknown condition {self.condition!r}")
        if self.spec not in ("oracle", "llm"):
            raise ValueError("spec must be oracle | llm")
        if self.injection_task in ("", "none"):
            self.injection_task = None

    def as_dict(self) -> dict:
        return asdict(self)


class Session:
    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.suite = load_suite(config.suite)
        self.user_task = self.suite.get_user_task_by_id(config.user_task)
        self.injection_task = (self.suite.get_injection_task_by_id(config.injection_task)
                               if config.injection_task else None)
        self.injections: dict[str, str] = {}
        if self.injection_task is not None:
            attack = get_cached_attack(config.suite, config.attack, REPLAY_PIPELINE_NAME)
            attack.model_name = config.attack_model_name
            self.injections = attack.attack(self.user_task, self.injection_task)
        environment = self.suite.load_and_inject_default_environment(self.injections)
        self.env = self.user_task.init_environment(environment)
        self.pre_env = self.env.model_copy(deep=True)

        cond = get_condition(config.condition)
        self.stats: dict = {}
        self.runtime = make_runtime(cond, self.suite.tools, suite_name=config.suite, prompt=self.user_task.PROMPT,
                                    stats=self.stats, record_env_hash=True)
        self.spec_source = config.spec if cond.gated else "oracle(audit)"
        provider = spec_provider(config.spec if cond.gated else "oracle", config.suite, config.user_task)
        started = time.perf_counter()
        spec, meta = provider(self.user_task.PROMPT)
        self.runtime.configure(self.user_task.PROMPT, spec, {**meta, "wall_ms": (time.perf_counter() - started) * 1000})
        self.lock = threading.Lock()
        self.result: dict | None = None
        self.started = time.perf_counter()

    # ------------------------------------------------------------------ what the agent sees
    @property
    def prompt(self) -> str:
        return self.user_task.PROMPT

    def tool_specs(self) -> list[dict]:
        return [{"name": f.name, "description": f.description, "parameters": f.parameters.model_json_schema()}
                for f in self.suite.tools]

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
            requested_calls = [FunctionCall(function=e.tool, args=e.args) for e in self.runtime.trace]
            output = [text_content_block_from_string(final_output or "")]
            result = score_task(self.suite, self.user_task, self.injection_task, output, self.pre_env, self.env,
                                requested_calls, self.runtime)
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
