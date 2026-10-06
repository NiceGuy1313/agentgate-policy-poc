"""Configuration facts that the policies rely on, checked against AgentDojo itself."""

import yaml
from agentdojo.functions_runtime import FunctionsRuntime

from agentgate import SUITES
from agentgate.config import CONFIG_DIR, named_source_rules, policies_config
from agentgate.suites import load_suite


def _all_tools():
    return {f.name for s in SUITES for f in load_suite(s).tools}


def test_config_tool_names_exist():
    tools = _all_tools()
    pc = policies_config()
    names = set(pc["P1"]["targets"]) | set(pc["external_sinks"]) | set(pc["P4"]["tools"]) | set(pc["P6"]["tools"])
    names |= set(pc["P7"]["message_tools"]) | set(pc["P7"]["visit_tools"]) | set(pc["P8"]["tools"]) | set(pc["P9"]["tools"])
    names |= set(pc["reversibility"]["irreversible"]) | set(pc["reversibility"]["reversible"])
    roles = yaml.safe_load((CONFIG_DIR / "tool_roles.yaml").read_text())
    names |= {t for v in roles["effect"].values() for t in v}
    names |= set(named_source_rules()["by_argument"]) | set(named_source_rules()["trusted_tools"])
    assert names - tools == set()


def test_trusted_tools_never_carry_injections():
    trusted = set(named_source_rules()["trusted_tools"])
    for sname in SUITES:
        s = load_suite(sname)
        canaries = {k: f"<CANARY---{k}--->" for k in s.get_injection_vector_defaults()}
        for ut in s.user_tasks.values():
            env = ut.init_environment(s.load_and_inject_default_environment(canaries))
            rt = FunctionsRuntime(s.tools)
            for c in ut.ground_truth(env.model_copy(deep=True)):
                out, _ = rt.run_function(env, c.function, c.args)
                if c.function in trusted:
                    assert "CANARY" not in repr(out), (sname, c.function)
        env = s.load_and_inject_default_environment(canaries)
        rt = FunctionsRuntime(s.tools)
        for f in s.tools:
            if f.name in trusted and not f.parameters.model_json_schema().get("required"):
                out, _ = rt.run_function(env, f.name, {})
                assert "CANARY" not in repr(out), (sname, f.name)


def test_every_tool_is_classified_for_reversibility_or_read_only():
    pc = policies_config()
    classified = set(pc["reversibility"]["irreversible"]) | set(pc["reversibility"]["reversible"])
    roles = yaml.safe_load((CONFIG_DIR / "tool_roles.yaml").read_text())
    effect = {t for v in roles["effect"].values() for t in v}
    assert effect <= classified
