"""버전과 무관한 Gate 공통부: 정책 primitive, 정책 엔진, 값 출처 인덱스, TaskSpec, `GatedRuntime`.

정책 판정 코드에는 LLM 호출을 넣지 않는다. 버전별 정책은 `agentgate.v1`·`v2`·`v3`에 있다.
"""

from agentgate.gate.base import (
    VERDICT_SEVERITY,
    Policy,
    PolicyContext,
    PolicyDecision,
    SessionState,
    ToolCall,
    Verdict,
    strictest_verdict,
)
from agentgate.gate.engine import AllowAllPolicyEngine, EngineResult, PolicyEngine, combine_verdicts

__all__ = ["AllowAllPolicyEngine", "EngineResult", "Policy", "PolicyContext", "PolicyDecision", "PolicyEngine",
           "SessionState", "ToolCall", "VERDICT_SEVERITY", "Verdict", "combine_verdicts", "strictest_verdict"]
