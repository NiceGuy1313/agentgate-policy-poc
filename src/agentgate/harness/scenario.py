"""One AgentDojo scenario behind the gate: environment + injections + GatedRuntime + TaskSpec + scoring.

`Session` is what every agent framework talks to (through `GateToolBridge`). It follows the same steps as
`agentgate.agentdojo.pipeline.run_live_task` / `scripts/evaluation/live_evaluation.run_one`, but leaves the agent loop to
the caller: the framework calls tools through `invoke` and hands in its final answer with `finish`.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass

from agentdojo.functions_runtime import FunctionCall
from agentdojo.types import text_content_block_from_string

from agentgate.agentdojo.pipeline import score_task
from agentgate.agentdojo.replay import REPLAY_PIPELINE_NAME, get_cached_attack
from agentgate.agentdojo.suites import load_suite
from agentgate.agents.factory import spec_provider
from agentgate.gate.runtime import GatedRuntime

# Condition -> (gate mode, escalate action). Same table as scripts/evaluation/live_evaluation.py.
CONDITIONS = {"C0": ("audit", "block"), "C1": ("enforce", "block"), "C2": ("enforce", "allow"),
              "C3": ("audit", "block"), "C4": ("enforce", "judge")}
GATED = ("C1", "C2", "C4")
FRAMEWORK_CONDITIONS = ("C0", "C1", "C2", "C4")   # C3 is an AgentDojo pipeline defense (native only)


def default_attack_model_name(provider: str, model: str) -> str:
    """Name filled into the attack template; same rule as live_evaluation.py."""
    return {"anthropic": "Claude", "openai": "GPT-4"}.get(provider, re.split(r"[-:_]", model)[0].upper()
                                                          or "AI assistant")


@dataclass
class ScenarioConfig:
    suite: str
    user_task: str
    injection_task: str | None = None
    condition: str = "C1"
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
    def __init__(self, config: ScenarioConfig, judge=None):
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

        mode, escalate_action = CONDITIONS[config.condition]
        self.judge = judge
        runtime_kwargs = {"mode": mode, "escalate_action": escalate_action, "record_env_hash": True}
        if config.condition == "C4":
            if judge is None:
                from agentgate.v1.judge import EscalationJudge
                self.judge = EscalationJudge()
            from agentgate.v1.judge import as_runtime_judge
            runtime_kwargs["judge"] = as_runtime_judge(self.judge, self.user_task.PROMPT)
        self.runtime = GatedRuntime(self.suite.tools, **runtime_kwargs)
        self.spec_source = config.spec if config.condition in GATED else "oracle(audit)"
        provider = spec_provider(config.spec if config.condition in GATED else "oracle", config.suite,
                                 config.user_task)
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
        """Gate decision only (no execution, no state change), e.g. for a Wrapper's own native tools."""
        from agentgate.gate.base import ToolCall
        with self.lock:
            return self.runtime.engine.decide(ToolCall(tool, dict(args or {})), self.runtime.context).as_dict()

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

    def record(self, **extra) -> dict:
        """Per-pair record in the live_evaluation.py schema (check_enforcement / summarize_live read it as is)."""
        result = self.result or {}
        return {
            "condition": self.config.condition, "suite": self.config.suite, "user_task": self.config.user_task,
            "injection_task": self.config.injection_task,
            "attack": self.config.attack if self.injection_task else None,
            "spec_source": self.spec_source, "spec_meta": self.runtime.spec_meta,
            "spec": self.runtime.spec.model_dump() if self.runtime.spec is not None else None,
            **result,
            "gate_ms": round(sum(e.decide_ms for e in self.runtime.trace), 3),
            "judge": self.judge.stats if self.judge is not None else None,
            "trace": self.runtime.trace_records(),
            **extra,
        }
