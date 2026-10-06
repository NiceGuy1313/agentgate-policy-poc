"""Thin helpers around AgentDojo suite loading (API notes 2-a, 2-e, 2-f)."""

from __future__ import annotations

from functools import lru_cache

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import TaskSuite
from agentdojo.types import get_text_content_as_str

from agentgate import BENCHMARK_VERSION


def load_suite(name: str, version: str = BENCHMARK_VERSION) -> TaskSuite:
    return get_suite(version, name)


def default_env(suite: TaskSuite):
    """Default environment with every injection vector at its default (benign) value."""
    return suite.load_and_inject_default_environment({})


def task_env(suite: TaskSuite, user_task, injections: dict[str, str] | None = None):
    env = suite.load_and_inject_default_environment(injections or {})
    return user_task.init_environment(env)


@lru_cache(maxsize=None)
def injection_candidates(suite_name: str, user_task_id: str, version: str = BENCHMARK_VERSION) -> tuple[str, ...]:
    """Injection vectors the user task's ground truth actually reads.

    Same procedure as agentdojo.attacks.base_attacks.BaseAttack.get_injection_candidates,
    re-implemented so that it can be used without an attack/pipeline object and without
    raising on non-injectable tasks.
    """
    suite = load_suite(suite_name, version)
    user_task = suite.get_user_task_by_id(user_task_id)
    canaries = {inj: f"<CANARY---{inj}--->" for inj in suite.get_injection_vector_defaults()}
    env = suite.load_and_inject_default_environment(canaries)
    _, _, _, responses, _ = GroundTruthPipeline(user_task).query(
        user_task.PROMPT, FunctionsRuntime(suite.tools), env
    )
    found = []
    for inj, canary in canaries.items():
        if any(r["content"] is not None and canary in get_text_content_as_str(r["content"]) for r in responses):
            found.append(inj)
    return tuple(found)


def tool_infos(suite: TaskSuite) -> list[dict]:
    return [{"name": f.name, "description": f.description} for f in suite.tools]
