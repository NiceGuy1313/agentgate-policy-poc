"""Gate 공통 자료형: 판정(Verdict), 툴 호출, 판정 문맥, 세션 상태. 이 패키지에서는 LLM·네트워크 호출을 하지 않는다."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from functools import lru_cache

from agentgate.core.binding import ToolBinding, default_tool_binding
from agentgate.core.provenance import ValueIndex
from agentgate.core.task_spec import TaskSpec


@dataclass(frozen=True)
class SessionBindings:
    """Which calls SessionState records, from the tool environment binding (`ToolBinding`: tool_args.yaml
    message_tools and tool_roles.yaml external_send), so the gate core names no tool."""

    sent_message_tools: frozenset[str]      # external_send tools of kind message: output id_ = a sent message

    @classmethod
    def from_binding(cls, binding: ToolBinding) -> "SessionBindings":
        return cls(frozenset(set(binding.effect_roles.get("external_send", [])) & binding.message_tools))

    @classmethod
    def from_config(cls) -> "SessionBindings":
        """The default (AgentDojo) binding."""
        return cls.from_binding(default_tool_binding())


@lru_cache(maxsize=1)
def default_session_bindings() -> SessionBindings:
    return SessionBindings.from_config()


@dataclass
class SessionState:
    """Per-task state: messages sent so far (R6 refuses deleting them)."""

    sent_message_ids: list[str] = field(default_factory=list)
    bindings: SessionBindings | None = None                   # None: default_session_bindings()

    def record_call(self, tool: str, raw_output) -> None:
        bindings = self.bindings or default_session_bindings()
        if tool in bindings.sent_message_tools and raw_output is not None:
            message_id = getattr(raw_output, "id_", None)
            if message_id is not None:
                self.sent_message_ids.append(str(message_id))


class Verdict(str, enum.Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass
class ToolCall:
    tool: str
    args: dict


@dataclass
class PolicyContext:
    """Everything a judgement may look at: the task spec, the trace's ValueIndex, the session state, the gate config
    and the user prompt the TaskSpec was extracted from."""

    spec: TaskSpec
    value_index: ValueIndex
    session: SessionState
    config: dict
    prompt: str = ""

