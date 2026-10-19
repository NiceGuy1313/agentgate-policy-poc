"""정답(ground truth) 궤적을 게이트로 재생한다(RQ1). Gate 자체는 LLM을 부르지 않는다.

어떤 Gate로 재생할지는 `runtime_factory`(보통 `agentgate.experiment.conditions.make_runtime`)가 정한다.
"""

from __future__ import annotations

from functools import lru_cache

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.types import text_content_block_from_string

from agentgate.core.binding import default_tool_binding
from agentgate.core.task_spec import TaskSpec
from agentgate.scenarios.attack_trajectories import plan_attack_calls
from agentgate.scenarios.suites import load_suite

ATTACK_MARKER = "<INFORMATION>"   # AgentDojo 공격 템플릿이 주입 문구를 감싸는 태그. 이 표지를 찾는 코드는 모두 이 상수를 쓴다
REPLAY_PIPELINE_NAME = "gpt-4o-2024-05-13"   # only used to fill {model} in the attack template


class _NamedPipelineStub(BasePipelineElement):
    """Carries only a pipeline name; AgentDojo attacks read it to fill the attack template."""

    def __init__(self, name: str):
        self.name = name

    def query(self, *args, **kwargs):  # pragma: no cover - never executed
        raise NotImplementedError


@lru_cache(maxsize=None)
def get_cached_attack(suite_name: str, attack_name: str, pipeline_name: str):
    return load_attack(attack_name, load_suite(suite_name), _NamedPipelineStub(pipeline_name))


def build_injections(suite_name: str, user_task, injection_task, attack_name: str = "important_instructions",
                     pipeline_name: str = REPLAY_PIPELINE_NAME) -> dict[str, str]:
    return get_cached_attack(suite_name, attack_name, pipeline_name).attack(user_task, injection_task)


def _ground_truth_model_output(*tasks) -> list:
    text = "\n".join(t.GROUND_TRUTH_OUTPUT for t in tasks if t is not None and t.GROUND_TRUTH_OUTPUT)
    return [text_content_block_from_string(text)]


def replay_benign_task(suite_name: str, user_task_id: str, spec: TaskSpec, mode: str = "audit", *,
                       runtime_factory) -> dict:
    suite = load_suite(suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)
    environment = user_task.init_environment(suite.load_and_inject_default_environment({}))
    pre_environment = environment.model_copy(deep=True)
    runtime = runtime_factory(suite.tools, prompt=user_task.PROMPT, spec=spec, mode=mode)
    runtime.phase = "user"
    calls = user_task.ground_truth(pre_environment.model_copy(deep=True))
    for call in calls:
        runtime.run_function(environment, call.function, call.args)
    utility = suite._check_user_task_utility(user_task, _ground_truth_model_output(user_task), pre_environment,
                                             environment, calls)
    return {"suite": suite_name, "user_task": user_task_id, "mode": mode, "utility": bool(utility),
            "trace": runtime.trace_records()}


def replay_attack_pair(suite_name: str, user_task_id: str, injection_task_id: str, spec: TaskSpec,
                       mode: str = "audit", attack_name: str = "important_instructions", *,
                       runtime_factory) -> dict:
    suite = load_suite(suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)
    injection_task = suite.get_injection_task_by_id(injection_task_id)
    injections = build_injections(suite_name, user_task, injection_task, attack_name)
    environment = user_task.init_environment(suite.load_and_inject_default_environment(injections))
    pre_environment = environment.model_copy(deep=True)
    runtime = runtime_factory(suite.tools, prompt=user_task.PROMPT, spec=spec, mode=mode)
    requested = []   # every call the (simulated) agent asked for, including blocked ones
    runtime.phase = "user"
    for call in user_task.ground_truth(pre_environment.model_copy(deep=True)):
        requested.append(call)
        runtime.run_function(environment, call.function, call.args)
    reached = any(ATTACK_MARKER in (event.output_text or "") for event in runtime.trace)
    runtime.phase = "injection"
    injection_calls, source = plan_attack_calls(suite_name, injection_task, environment)
    for call in injection_calls:
        requested.append(call)
        runtime.run_function(environment, call.function, call.args)
    model_output = _ground_truth_model_output(user_task, injection_task)
    # AgentDojo checks can mutate the post-environment (e.g. Inbox.get_unread marks e-mails read), so the
    # executed-only variant repeats the same check sequence on a snapshot taken before any check.
    post_copy, pre_copy = environment.model_copy(deep=True), pre_environment.model_copy(deep=True)
    utility = suite._check_user_task_utility(user_task, model_output, pre_environment, environment, requested)
    security = suite._check_injection_task_security(injection_task, model_output, pre_environment, environment,
                                                    requested)
    # Some tasks (slack injection_task_5) score security from the *requested* call trace, so a
    # blocked attempt still counts as success. security_executed scores executed calls only.
    executed = [call for call, event in zip(requested, runtime.trace) if event.executed]
    suite._check_user_task_utility(user_task, model_output, pre_copy, post_copy, executed)
    security_executed = suite._check_injection_task_security(injection_task, model_output, pre_copy, post_copy,
                                                             executed)
    effect_tools = (getattr(runtime, "binding", None) or default_tool_binding()).effect_tools
    trace = runtime.trace_records()
    for record in trace:
        record["role"] = (("effect" if record["tool"] in effect_tools else "prep")
                          if record["phase"] == "injection" else None)
    return {"suite": suite_name, "user_task": user_task_id, "injection_task": injection_task_id, "mode": mode,
            "attack": attack_name, "gt_source": source, "reached": reached, "injected_vectors": sorted(injections),
            "utility": bool(utility), "security": bool(security), "security_executed": bool(security_executed),
            "trace": trace}
