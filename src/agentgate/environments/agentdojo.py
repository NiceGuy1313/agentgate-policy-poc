"""AgentDojo 환경 바인딩: suite 로딩, 인젝션, 환경, 툴 schema, 채점, TaskSpec 출처를 `agentgate.scenarios`에 위임한다.

툴 바인딩은 config/agentdojo/(`ToolBinding`)다. 공격 목적지 목록(`ATTACKER_DESTINATIONS`)은 live run 분석
heuristic(`agentgate.experiment.live_run_analysis`)이 쓴다.
"""

from __future__ import annotations

from agentgate.core.binding import ToolBinding, default_tool_binding
from agentgate.core.task_spec import TaskSpec

ATTACKER_DESTINATIONS = [   # 공격 태스크의 외부 목적지. 검증 결과: data/agentdojo/benchmark/attacker_dest_check.json
    "mark.black-2134@gmail.com", "jay@google.com", "janeLong@google.com", "US133000000121212121212",
    "www.secure-systems-252.com", "www.my-website-234.com/random", "www.true-informations.com",
    "fred9246@gmail.com", "https://bit.ly/sUf7tOJ",
]


def spec_provider(kind: str, suite_name: str, user_task_id: str):
    """TaskSpec 출처: oracle(data/agentdojo/oracle_specs) 또는 llm(TaskSpecExtractor, prompts/extract_taskspec.md)."""
    from agentgate.scenarios.task_specs import load_task_spec

    def oracle(prompt):
        return (load_task_spec("oracle", suite_name, user_task_id),
                {"source": "oracle", "cached": True, "latency_ms": 0.0})

    if kind == "oracle":
        return oracle

    def llm(prompt):
        from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
        from agentgate.scenarios.suites import describe_tools, load_suite
        tools = [ToolInfo(**tool) for tool in describe_tools(load_suite(suite_name))]
        result = TaskSpecExtractor().extract(prompt, tools)
        meta = {"source": f"llm:{result.model}", "cached": result.cached,
                "latency_ms": 0.0 if result.cached else result.latency_ms,
                "input_tokens": 0 if result.cached else result.input_tokens,
                "output_tokens": 0 if result.cached else result.output_tokens,
                "error": result.error}
        # Extraction failure -> fail closed with an empty spec (nothing requested).
        return (result.spec or TaskSpec(notes="extraction failed")), meta

    return llm


class AgentDojoScenarioRun:
    """One AgentDojo (suite, user task, injection task) with its injected environment."""

    def __init__(self, config):
        from agentgate.scenarios.replay import REPLAY_PIPELINE_NAME, get_cached_attack
        from agentgate.scenarios.suites import load_suite

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

    @property
    def prompt(self) -> str:
        return self.user_task.PROMPT

    @property
    def functions(self):
        return self.suite.tools

    @property
    def injection_goal(self) -> str | None:
        return getattr(self.injection_task, "GOAL", None)

    def tool_schemas(self) -> list[dict]:
        return [{"name": f.name, "description": f.description, "parameters": f.parameters.model_json_schema()}
                for f in self.suite.tools]

    # ---------------------------------------------------------------- scripted agents (fake LLM)
    def ground_truth_calls(self) -> list:
        """The user task's reference calls, planned on the current environment."""
        return list(self.user_task.ground_truth(self.env.model_copy(deep=True)))

    def attack_calls(self) -> list:
        """The injection task's calls a compromised agent makes, planned on the current environment."""
        from agentgate.scenarios.attack_trajectories import plan_attack_calls
        calls, _ = plan_attack_calls(self.config.suite, self.injection_task, self.env)
        return list(calls)

    def ground_truth_output(self, hijacked: bool = False) -> str:
        outputs = [self.user_task.GROUND_TRUTH_OUTPUT]
        if hijacked and self.injection_task is not None:
            outputs.append(self.injection_task.GROUND_TRUTH_OUTPUT)
        return "\n".join(o for o in outputs if o)

    # ---------------------------------------------------------------- scoring
    def score(self, final_output: str | None, runtime) -> dict:
        from agentdojo.functions_runtime import FunctionCall
        from agentdojo.types import text_content_block_from_string

        from agentgate.scenarios.pipeline import score_task
        requested_calls = [FunctionCall(function=e.tool, args=e.args) for e in runtime.trace]
        output = [text_content_block_from_string(final_output or "")]
        return score_task(self.suite, self.user_task, self.injection_task, output, self.pre_env, self.env,
                          requested_calls, runtime)


class AgentDojoEnvironment:
    name = "agentdojo"
    attacker_destinations = ATTACKER_DESTINATIONS

    def __init__(self, binding: ToolBinding | None = None):
        self.binding = binding or default_tool_binding()

    def tool_infos(self, suite: str) -> list[dict]:
        from agentgate.scenarios.suites import describe_tools, load_suite
        return describe_tools(load_suite(suite))

    def oracle_taskspec(self, suite: str, user_task: str) -> TaskSpec:
        from agentgate.scenarios.task_specs import load_task_spec
        return load_task_spec("oracle", suite, user_task)

    def spec_provider(self, kind: str, suite: str, user_task: str):
        return spec_provider(kind, suite, user_task)

    def open(self, config) -> AgentDojoScenarioRun:
        return AgentDojoScenarioRun(config)


__all__ = ["ATTACKER_DESTINATIONS", "AgentDojoEnvironment", "AgentDojoScenarioRun", "spec_provider"]
