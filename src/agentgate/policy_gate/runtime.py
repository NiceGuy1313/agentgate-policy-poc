"""정책 Gate 런타임: effect 호출을 실행 직전에 판정기(판정 LLM 또는 룰북 정적 코드)에 보내고 그 답(ALLOW / BLOCK)을 집행한다.

읽기 호출은 판정 없이 실행한다. 실행된 툴 응답마다 ToolSpec(JSON)을 붙여 다음 판정의 입력으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable

from agentgate.core.engine import AllowAllPolicyEngine, EngineResult
from agentgate.core.runtime import GatedRuntime, TraceEvent
from agentgate.core.types import ToolCall, Verdict
from agentgate.policy_gate import effect_tools


class PolicyGateRuntime(GatedRuntime):
    """`toolspec`: (tool, args, output_text) -> validated ToolSpec dict | None (`agentgate.policy_gate.toolspec`).
    `judge`: (call, result, context) -> judge record (`agentgate.policy_gate.llm_judge`)."""

    def __init__(self, functions=(), *, toolspec: Callable[[str, dict, str], dict | None] | None = None, **kwargs):
        kwargs.setdefault("engine", AllowAllPolicyEngine())
        super().__init__(functions, **kwargs)
        self.toolspec = toolspec
        self.effect_tools = effect_tools()
        self.trace_label_keys = ("toolspec",) if toolspec is not None else ()

    def resolve(self, call: ToolCall, result: EngineResult) -> tuple[Verdict, dict | None]:
        if call.tool not in self.effect_tools:
            return Verdict.ALLOW, None
        if self.judge is None or self.escalate_action != "judge":
            if self.mode == "enforce":   # an enforcing policy gate without a judge would allow every effect call
                raise RuntimeError(f"policy gate in enforce mode needs escalate_action 'judge' and a judge "
                                   f"(got {self.escalate_action!r}, judge {'set' if self.judge else 'None'})")
            return Verdict.ALLOW, None
        judgement = self.judge(call, result, self.context)
        return Verdict(judgement["verdict"]), judgement

    def label_response(self, source, event: TraceEvent) -> None:
        if self.toolspec is None:
            return
        spec = self.toolspec(event.tool, event.args, event.output_text)
        source.labels["toolspec"] = spec
        event.labels["toolspec"] = {"ok": spec is not None, "records": len((spec or {}).get("records", [])),
                                    "requests": len((spec or {}).get("requests", []))}

    def judge_stats(self) -> dict:
        """Judge totals over this run's trace (calls, API calls, tokens, latency)."""
        judgements = [e.decision["judge"] for e in self.trace if (e.decision or {}).get("judge")]
        return {"judge": judgements[0].get("judge") if judgements else None, "calls": len(judgements),
                "api_calls": sum(1 for j in judgements if j.get("answer") and not j.get("cached")),
                "input_tokens": sum(j.get("input_tokens", 0) for j in judgements),
                "output_tokens": sum(j.get("output_tokens", 0) for j in judgements),
                "cache_read_input_tokens": sum(j.get("cache_read_input_tokens", 0) for j in judgements),
                "cache_creation_input_tokens": sum(j.get("cache_creation_input_tokens", 0) for j in judgements),
                "latency_ms": round(sum(j.get("latency_ms", 0.0) for j in judgements), 1)}
