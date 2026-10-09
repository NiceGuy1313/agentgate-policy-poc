"""모든 injection task의 참조 공격 궤적이 기본 환경에서 injection goal을 달성하는지 검사한다."""

import pytest
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from agentgate import SUITES
from agentgate.evaluation.attack_trajectories import plan_attack_calls
from agentgate.suites import load_default_environment, load_suite

CASES = [(s, t) for s in SUITES for t in load_suite(s).injection_tasks]


@pytest.mark.parametrize("suite_name,task_id", CASES)
def test_reference_trajectory_achieves_injection_goal(suite_name, task_id):
    suite = load_suite(suite_name)
    injection_task = suite.get_injection_task_by_id(task_id)
    environment = load_default_environment(suite)
    pre_environment = environment.model_copy(deep=True)
    calls, _ = plan_attack_calls(suite_name, injection_task, environment)
    runtime = FunctionsRuntime(suite.tools)
    for call in calls:
        _, error = runtime.run_function(environment, call.function, call.args)
        assert error is None, error
    output = [text_content_block_from_string(injection_task.GROUND_TRUTH_OUTPUT)]
    assert suite._check_injection_task_security(injection_task, output, pre_environment, environment, calls)
