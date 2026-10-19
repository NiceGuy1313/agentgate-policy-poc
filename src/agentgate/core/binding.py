"""툴 환경 바인딩(`ToolBinding`): 툴 이름을 아는 설정(effect 역할, 인자 역할, 지목 자료 규칙, 신뢰 툴, 민감 읽기)을 한 객체로 묶는다.

Gate core와 정책 코드는 툴 이름을 직접 적지 않고 이 바인딩만 읽는다. 기본값은 AgentDojo 바인딩(config/agentdojo/)이다.
환경 등록부는 `agentgate.environments`다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from agentgate.config import CONFIG_DIR

DEFAULT_BINDING_DIR = CONFIG_DIR / "agentdojo"


@dataclass(frozen=True, eq=False)
class ToolBinding:
    """One tool environment's bindings, read from a directory with tool_roles.yaml, tool_args.yaml,
    named_source_rules.yaml and sensitive_reads.yaml."""

    name: str
    effect_roles: dict[str, list[str]]        # effect category -> tool names (tool_roles.yaml effect)
    tool_args: dict                           # tools (argument roles), message_tools, outgoing, R4 (tool_args.yaml)
    named_by_argument: dict[str, list[str]]   # tool -> arguments naming a source (named_source_rules.yaml by_argument)
    trusted_tools: frozenset[str]             # directory tools (named_source_rules.yaml trusted_tools)
    sensitive: dict[str, list[str]]           # sensitive read category -> tool names (sensitive_reads.yaml)

    @classmethod
    def from_dir(cls, path: str | Path, name: str | None = None) -> ToolBinding:
        path = Path(path)

        def load(file: str):
            return yaml.safe_load((path / file).read_text())

        named = load("named_source_rules.yaml")
        return cls(name=name or path.name, effect_roles=load("tool_roles.yaml")["effect"],
                   tool_args=load("tool_args.yaml"), named_by_argument=named["by_argument"],
                   trusted_tools=frozenset(named.get("trusted_tools", [])), sensitive=load("sensitive_reads.yaml"))

    @property
    def effect_tools(self) -> frozenset[str]:
        """Calls that change state, send something out or visit a URL."""
        return frozenset(tool for tools in self.effect_roles.values() for tool in tools)

    @property
    def arg_roles(self) -> dict[str, dict]:
        """Effect tool -> {role: [argument names]}."""
        return self.tool_args["tools"]

    @property
    def outgoing(self) -> frozenset[str]:
        """Tools whose content leaves the user (R4, R7)."""
        return frozenset(self.tool_args.get("outgoing", []))

    @property
    def message_tools(self) -> frozenset[str]:
        return frozenset(self.tool_args.get("message_tools", []))

    @property
    def r4(self) -> dict:
        return self.tool_args.get("R4", {})

    def sensitive_categories(self, tool: str) -> list[str]:
        return [category for category, tools in self.sensitive.items() if tool in tools]


@lru_cache(maxsize=1)
def default_tool_binding() -> ToolBinding:
    """The AgentDojo binding (config/agentdojo/), read once."""
    return ToolBinding.from_dir(DEFAULT_BINDING_DIR, "agentdojo")


__all__ = ["DEFAULT_BINDING_DIR", "ToolBinding", "default_tool_binding"]
