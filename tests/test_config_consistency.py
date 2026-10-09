"""정책이 전제하는 설정 사실을 AgentDojo 자체와 대조해 검사한다."""

import yaml
from agentdojo.functions_runtime import FunctionsRuntime

from agentgate import SUITES
from agentgate.config import CONFIG_DIR, load_named_source_rules, load_policy_config
from agentgate.suites import load_suite


def _all_tools():
    return {f.name for s in SUITES for f in load_suite(s).tools}


def test_config_tool_names_exist():
    tools = _all_tools()
    policy_config = load_policy_config()
    names = set(policy_config["P1"]["targets"]) | set(policy_config["external_sinks"])
    names |= set(policy_config["P4"]["tools"]) | set(policy_config["P6"]["tools"])
    names |= set(policy_config["P7"]["message_tools"]) | set(policy_config["P7"]["visit_tools"])
    names |= set(policy_config["P8"]["tools"]) | set(policy_config["P9"]["tools"])
    names |= set(policy_config["reversibility"]["irreversible"]) | set(policy_config["reversibility"]["reversible"])
    roles = yaml.safe_load((CONFIG_DIR / "tool_roles.yaml").read_text())
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


def test_every_tool_is_classified_for_reversibility_or_read_only():
    policy_config = load_policy_config()
    classified = set(policy_config["reversibility"]["irreversible"]) | set(policy_config["reversibility"]["reversible"])
    roles = yaml.safe_load((CONFIG_DIR / "tool_roles.yaml").read_text())
    effect = {t for v in roles["effect"].values() for t in v}
    assert effect <= classified
