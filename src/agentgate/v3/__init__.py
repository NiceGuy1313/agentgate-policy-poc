"""v3: 정책 엔진 없이 판정 LLM이 effect 호출을 판단한다 (live C9, C9n).

입력은 TaskSpec(사용자 프롬프트에서 추출), ToolSpec(툴 응답에서 추출, `agentgate.v3.toolspec`), 대기 중 호출이고, C9는
정책 문서(config/v3/policy.yaml)를 함께 받는다. 읽기 호출은 판정하지 않는다. TaskSpec 추출 프롬프트: prompts/v3/extract_taskspec.md.
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml

VERSION = "v3"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v3" / "extract_taskspec.md"
POLICY_FILE = "v3/policy.yaml"


def load_rules() -> list[dict]:
    return load_config_yaml(POLICY_FILE)["rules"]


def effect_tools() -> set[str]:
    """Calls that change state, send something out or visit a URL (config/agentdojo/tool_roles.yaml)."""
    return {tool for tools in load_config_yaml("agentdojo/tool_roles.yaml")["effect"].values() for tool in tools}


__all__ = ["POLICY_FILE", "TASKSPEC_PROMPT", "VERSION", "effect_tools", "load_rules"]
