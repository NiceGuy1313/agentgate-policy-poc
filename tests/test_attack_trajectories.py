import pytest
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from agentgate import SUITES
from agentgate.attack_trajectories import attack_calls
from agentgate.suites import default_env, load_suite

CASES = [(s, t) for s in SUITES for t in load_suite(s).injection_tasks]


@pytest.mark.parametrize("suite_name,task_id", CASES)
def test_reference_trajectory_achieves_injection_goal(suite_name, task_id):
    s = load_suite(suite_name)
    it = s.get_injection_task_by_id(task_id)
    env = default_env(s)
    pre = env.model_copy(deep=True)
    calls, _ = attack_calls(suite_name, it, env)
    rt = FunctionsRuntime(s.tools)
    for c in calls:
        _, err = rt.run_function(env, c.function, c.args)
        assert err is None, err
    out = [text_content_block_from_string(it.GROUND_TRUTH_OUTPUT)]
    assert s._check_injection_task_security(it, out, pre, env, calls)
