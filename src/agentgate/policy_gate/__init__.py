"""정책 Gate: effect 호출을 룰북(config/rulebook.yaml, R1–R10) 기준으로 실행 직전에 ALLOW / BLOCK 판정한다.

입력은 TaskSpec(사용자 프롬프트에서 추출), ToolSpec(툴 응답에서 추출, `agentgate.policy_gate.toolspec`), 대기 중 호출이다.
판정기는 둘이다. `gate_llm_judge`는 판정 LLM이 룰북을 읽고 판단하고(`agentgate.policy_gate.llm_judge`),
`gate_static_rules`는 같은 룰북을 정적 코드로 검사한다(`agentgate.policy_gate.rule_checker`, 툴 인자 역할은
config/agentdojo/tool_args.yaml). 읽기 호출은 판정하지 않는다. TaskSpec 추출 프롬프트는 prompts/extract_taskspec.md다.
"""

from __future__ import annotations

from agentgate.config import TOOL_ARGS_FILE, load_config_yaml, load_tool_args, load_tool_roles

RULEBOOK_FILE = "rulebook.yaml"


def load_rulebook() -> list[dict]:
    """Rules R1–R10 of config/rulebook.yaml."""
    return load_config_yaml(RULEBOOK_FILE)["rules"]


def tool_roles() -> dict[str, list[str]]:
    """Effect categories of config/agentdojo/tool_roles.yaml: category -> tool names."""
    return load_tool_roles()


def effect_tools() -> set[str]:
    """Calls that change state, send something out or visit a URL (config/agentdojo/tool_roles.yaml)."""
    return {tool for tools in tool_roles().values() for tool in tools}


__all__ = ["RULEBOOK_FILE", "TOOL_ARGS_FILE", "effect_tools", "load_rulebook", "load_tool_args", "tool_roles"]
