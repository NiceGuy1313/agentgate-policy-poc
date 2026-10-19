"""평가 환경 인터페이스: 툴 바인딩(`ToolBinding`)과 시나리오 하나의 실행 단위(`ScenarioRun`)를 정한다.

Gate core·정책 코드는 `ToolBinding`만 받는다. 시나리오 로딩(툴, 환경, 인젝션), TaskSpec 출처, 채점은 `Environment`
구현(`agentgate.environments.agentdojo`)이 맡는다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentgate.core.binding import ToolBinding, default_tool_binding


@runtime_checkable
class ScenarioRun(Protocol):
    """One opened scenario: what the agent sees, the live environment the tools mutate, and scoring."""

    prompt: str
    functions: Any                 # tool functions handed to the gated runtime
    env: Any                       # live environment (mutated by tool calls)
    injections: dict[str, str]     # injection vector -> injected text
    injection_goal: str | None

    def tool_schemas(self) -> list[dict]:
        """[{name, description, parameters (JSON schema)}] the agent may call."""

    def score(self, final_output: str | None, runtime) -> dict:
        """utility / security (/ security_executed) of the finished run, from the runtime's trace."""


@runtime_checkable
class Environment(Protocol):
    name: str
    binding: ToolBinding

    def tool_infos(self, suite: str) -> list[dict]:
        """[{name, description}] of a suite's tools (TaskSpec / ToolSpec extractor input)."""

    def oracle_taskspec(self, suite: str, user_task: str):
        """The reviewed TaskSpec of a user task."""

    def open(self, config) -> ScenarioRun:
        """Load one scenario (`agentgate.harness.session.ScenarioConfig`)."""


__all__ = ["Environment", "ScenarioRun", "ToolBinding", "default_tool_binding"]
