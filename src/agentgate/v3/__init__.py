"""v3: 정책 엔진 없이 effect 호출을 룰북(config/v3/rulebook.yaml, R1–R10) 기준으로 ALLOW / BLOCK 판정한다.

입력은 TaskSpec(사용자 프롬프트에서 추출), ToolSpec(툴 응답에서 추출, `agentgate.v3.toolspec`), 대기 중 호출이다.
방식 A(C10a)는 판정 LLM이 룰북을 읽고 판단하고(`agentgate.v3.judge`), 방식 B(C10b)는 같은 룰북을 정적 코드로
검사한다(`agentgate.v3.rules`, 툴 인자 역할은 config/v3/tool_args.yaml). 읽기 호출은 판정하지 않는다.
TaskSpec 추출 프롬프트: prompts/v3/extract_taskspec.md.
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml

VERSION = "v3"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v3" / "extract_taskspec.md"
RULEBOOK_FILE = "v3/rulebook.yaml"
TOOL_ARGS_FILE = "v3/tool_args.yaml"


def load_rules() -> list[dict]:
    return load_config_yaml(RULEBOOK_FILE)["rules"]


def load_tool_args() -> dict:
    return load_config_yaml(TOOL_ARGS_FILE)


def tool_roles() -> dict[str, list[str]]:
    """Effect categories of config/agentdojo/tool_roles.yaml: category -> tool names."""
    return load_config_yaml("agentdojo/tool_roles.yaml")["effect"]


def effect_tools() -> set[str]:
    """Calls that change state, send something out or visit a URL (config/agentdojo/tool_roles.yaml)."""
    return {tool for tools in tool_roles().values() for tool in tools}


__all__ = ["RULEBOOK_FILE", "TASKSPEC_PROMPT", "TOOL_ARGS_FILE", "VERSION", "effect_tools", "load_rules",
           "load_tool_args", "tool_roles"]
