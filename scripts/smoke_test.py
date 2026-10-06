"""Stage 0 task 3: for each suite, run one user task's ground truth through FunctionsRuntime on the
default environment and print the utility verdict. No LLM calls."""

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.suites import load_suite

print(f"benchmark {BENCHMARK_VERSION}")
for name in SUITES:
    suite = load_suite(name)
    ut = suite.get_user_task_by_id("user_task_0")
    env = ut.init_environment(suite.load_and_inject_default_environment({}))
    pre = env.model_copy(deep=True)
    rt = FunctionsRuntime(suite.tools)
    calls = ut.ground_truth(pre.model_copy(deep=True))
    for c in calls:
        rt.run_function(env, c.function, c.args, raise_on_error=True)
    ok = suite._check_user_task_utility(ut, [text_content_block_from_string(ut.GROUND_TRUTH_OUTPUT)], pre, env, calls)
    print(f"{name:10s} {ut.ID}: {len(calls)} calls, utility={ok}")
