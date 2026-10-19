"""Gate 설정(config/agentdojo/)이 전제하는 사실을 AgentDojo 자체와 대조해 검사한다."""

import yaml
from agentdojo.functions_runtime import FunctionsRuntime

from agentgate import SUITES
from agentgate.config import CONFIG_DIR, load_named_source_rules
from agentgate.policy_gate import load_tool_args
from agentgate.scenarios.suites import load_suite


def _all_tools():
    return {f.name for s in SUITES for f in load_suite(s).tools}


def test_config_tool_names_exist():
    tools = _all_tools()
    tool_args = load_tool_args()
    names = set(tool_args["tools"]) | set(tool_args["outgoing"])
    roles = yaml.safe_load((CONFIG_DIR / "agentdojo" / "tool_roles.yaml").read_text())
    names |= {t for v in roles["effect"].values() for t in v}
    names |= set(load_named_source_rules()["by_argument"]) | set(load_named_source_rules()["trusted_tools"])
    assert names - tools == set()


def test_trusted_tools_never_carry_injections():
    trusted = set(load_named_source_rules()["trusted_tools"])
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        canaries = {k: f"<CANARY---{k}--->" for k in suite.get_injection_vector_defaults()}
        for user_task in suite.user_tasks.values():
            environment = user_task.init_environment(suite.load_and_inject_default_environment(canaries))
            runtime = FunctionsRuntime(suite.tools)
            for call in user_task.ground_truth(environment.model_copy(deep=True)):
                output, _ = runtime.run_function(environment, call.function, call.args)
                if call.function in trusted:
                    assert "CANARY" not in repr(output), (suite_name, call.function)
        environment = suite.load_and_inject_default_environment(canaries)
        runtime = FunctionsRuntime(suite.tools)
        for function in suite.tools:
            if function.name in trusted and not function.parameters.model_json_schema().get("required"):
                output, _ = runtime.run_function(environment, function.name, {})
                assert "CANARY" not in repr(output), (suite_name, function.name)


def test_every_effect_tool_has_argument_roles():
    roles = yaml.safe_load((CONFIG_DIR / "agentdojo" / "tool_roles.yaml").read_text())
    effect = {t for v in roles["effect"].values() for t in v}
    assert effect <= set(load_tool_args()["tools"])
