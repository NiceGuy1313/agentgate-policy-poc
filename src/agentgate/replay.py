"""Ground-truth trajectory replay through the gate (Stage 5). No LLM calls."""

from __future__ import annotations

from functools import lru_cache

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.types import text_content_block_from_string

from agentgate.attack_trajectories import attack_calls
from agentgate.config import effect_tools
from agentgate.policy.engine import PolicyEngine
from agentgate.runtime import GatedRuntime
from agentgate.spec import TaskSpec
from agentgate.suites import load_suite

ATTACK_MARKER = "<INFORMATION>"
REPLAY_PIPELINE_NAME = "gpt-4o-2024-05-13"   # only used to fill {model} in the attack template


class _NamedPipeline(BasePipelineElement):
    def __init__(self, name: str):
        self.name = name

    def query(self, *a, **k):  # pragma: no cover - never executed
        raise NotImplementedError


@lru_cache(maxsize=None)
def _attack(suite_name: str, attack_name: str, pipeline_name: str):
    return load_attack(attack_name, load_suite(suite_name), _NamedPipeline(pipeline_name))


def injections_for(suite_name: str, user_task, injection_task, attack_name: str = "important_instructions",
                   pipeline_name: str = REPLAY_PIPELINE_NAME) -> dict[str, str]:
    return _attack(suite_name, attack_name, pipeline_name).attack(user_task, injection_task)


def _model_output(*tasks) -> list:
    text = "\n".join(t.GROUND_TRUTH_OUTPUT for t in tasks if t is not None and t.GROUND_TRUTH_OUTPUT)
    return [text_content_block_from_string(text)]


def run_benign(suite_name: str, user_task_id: str, spec: TaskSpec, mode: str = "audit",
               disabled: set[str] | None = None) -> dict:
    suite = load_suite(suite_name)
    ut = suite.get_user_task_by_id(user_task_id)
    env = ut.init_environment(suite.load_and_inject_default_environment({}))
    pre = env.model_copy(deep=True)
    rt = GatedRuntime(suite.tools, prompt=ut.PROMPT, spec=spec, engine=PolicyEngine(disabled=disabled), mode=mode)
    rt.phase = "user"
    calls = ut.ground_truth(pre.model_copy(deep=True))
    for c in calls:
        rt.run_function(env, c.function, c.args)
    utility = suite._check_user_task_utility(ut, _model_output(ut), pre, env, calls)
    return {"suite": suite_name, "user_task": user_task_id, "mode": mode, "utility": bool(utility),
            "trace": rt.trace_records()}


def run_attack(suite_name: str, user_task_id: str, injection_task_id: str, spec: TaskSpec, mode: str = "audit",
               disabled: set[str] | None = None, attack_name: str = "important_instructions") -> dict:
    suite = load_suite(suite_name)
    ut = suite.get_user_task_by_id(user_task_id)
    it = suite.get_injection_task_by_id(injection_task_id)
    injections = injections_for(suite_name, ut, it, attack_name)
    env = ut.init_environment(suite.load_and_inject_default_environment(injections))
    pre = env.model_copy(deep=True)
    rt = GatedRuntime(suite.tools, prompt=ut.PROMPT, spec=spec, engine=PolicyEngine(disabled=disabled), mode=mode)
    requested = []   # every call the (simulated) agent asked for, incl. blocked ones
    rt.phase = "user"
    for c in ut.ground_truth(pre.model_copy(deep=True)):
        requested.append(c)
        rt.run_function(env, c.function, c.args)
    reached = any(ATTACK_MARKER in (e.output_text or "") for e in rt.trace)
    rt.phase = "injection"
    inj_calls, source = attack_calls(suite_name, it, env)
    for c in inj_calls:
        requested.append(c)
        rt.run_function(env, c.function, c.args)
    out = _model_output(ut, it)
    utility = suite._check_user_task_utility(ut, out, pre, env, requested)
    security = suite._check_injection_task_security(it, out, pre, env, requested)
    # Some tasks (slack injection_task_5) score security from the *requested* call trace, so a
    # blocked attempt still counts as success. security_executed scores executed calls only.
    executed = [c for c, e in zip(requested, rt.trace) if e.executed]
    security_executed = suite._check_injection_task_security(it, out, pre, env, executed)
    eff = effect_tools()
    trace = rt.trace_records()
    for r in trace:
        r["role"] = ("effect" if r["tool"] in eff else "prep") if r["phase"] == "injection" else None
    return {"suite": suite_name, "user_task": user_task_id, "injection_task": injection_task_id, "mode": mode,
            "attack": attack_name, "gt_source": source, "reached": reached, "injected_vectors": sorted(injections),
            "utility": bool(utility), "security": bool(security), "security_executed": bool(security_executed),
            "trace": trace}
