"""AgentDojo setup check: for each suite, run one user task's ground truth through FunctionsRuntime on the
default environment and print the utility verdict. No LLM calls.

Usage: python scripts/catalog/check_agentdojo_setup.py
"""

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.suites import load_suite


def main() -> None:
    print(f"benchmark {BENCHMARK_VERSION}")
    for name in SUITES:
        suite = load_suite(name)
        user_task = suite.get_user_task_by_id("user_task_0")
        env = user_task.init_environment(suite.load_and_inject_default_environment({}))
        pre_environment = env.model_copy(deep=True)
        runtime = FunctionsRuntime(suite.tools)
        calls = user_task.ground_truth(pre_environment.model_copy(deep=True))
        for call in calls:
            runtime.run_function(env, call.function, call.args, raise_on_error=True)
        utility = suite._check_user_task_utility(
            user_task, [text_content_block_from_string(user_task.GROUND_TRUTH_OUTPUT)], pre_environment, env, calls
        )
        print(f"{name:10s} {user_task.ID}: {len(calls)} calls, utility={utility}")


if __name__ == "__main__":
    main()
