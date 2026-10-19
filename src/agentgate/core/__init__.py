"""Gate 공통부: 자료형, 값 출처 인덱스, TaskSpec, 단일 집행 지점 `GatedRuntime`.

판정 코드에는 LLM 호출을 넣지 않는다. 판정기(룰북 정적 코드, 판정 LLM)는 `agentgate.policy_gate`에 있다.
"""

from agentgate.core.types import PolicyContext, SessionState, ToolCall, Verdict

__all__ = ["PolicyContext", "SessionState", "ToolCall", "Verdict"]
