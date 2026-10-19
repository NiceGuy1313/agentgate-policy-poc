"""Gate trace 자료형과 직렬화: 툴 호출 기록(`TraceEvent`), 인자·출력의 평범한 자료 변환, 환경 해시, 차단 메시지.

`agentgate.core.runtime.GatedRuntime`이 쓴다(이 이름들은 runtime에서도 그대로 import할 수 있다).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentdojo.functions_runtime import FunctionCall, TaskEnvironment


@dataclass
class TraceEvent:
    step: int
    tool: str
    args: dict
    raw_output: Any = None
    output_text: str = ""
    error: str | None = None
    blocked: bool = False
    executed: bool = False
    phase: str = ""                   # replay bookkeeping: "user" | "injection" | "agent"
    decision: dict | None = None      # {"verdict", "decisions", "judge", "effective"}: see run_function
    decide_ms: float = 0.0
    labels: dict = field(default_factory=dict)   # label summaries of this response (ToolSpec)
    env_hash_before: str | None = None


def to_plain_data(value: Any) -> Any:
    if isinstance(value, FunctionCall):
        return {"__call__": value.function, "args": to_plain_data(dict(value.args))}
    if isinstance(value, Mapping):
        return {k: to_plain_data(item) for k, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


_to_plain_data = to_plain_data   # former private name


def compute_environment_hash(environment: TaskEnvironment | None) -> str | None:
    if environment is None:
        return None
    return hashlib.sha1(environment.model_dump_json().encode()).hexdigest()


BLOCK_REASON_CHARS = 240
_RULE_ID = re.compile(r"\bR(?:10|[1-9])\b")


def format_judge_block_message(judgement: dict) -> str:
    """What the agent receives when the judge blocks: rule ids and a short one-line reason from the judge record,
    e.g. "Tool call blocked by policy gate (R2, R3): ...". The full judge record stays in the trace only."""
    reason = " ".join(str(judgement.get("reason") or judgement.get("text") or "").split())
    reason = re.sub(r"^[*#`_ ]*(?:BLOCK|ALLOW)\b[\s:.\-]*", "", reason, flags=re.IGNORECASE)   # LLM answer word
    rule_ids = list(judgement.get("rule_ids") or dict.fromkeys(_RULE_ID.findall(reason)))
    if len(reason) > BLOCK_REASON_CHARS:
        reason = reason[:BLOCK_REASON_CHARS - 1].rstrip() + "…"
    label = f" ({', '.join(rule_ids)})" if rule_ids else ""
    return f"Tool call blocked by policy gate{label}: {reason or 'the call is outside the requested task'}"


NO_TASKSPEC_MESSAGE = "Tool call blocked by policy gate: no TaskSpec is configured for this task (fail closed)."
