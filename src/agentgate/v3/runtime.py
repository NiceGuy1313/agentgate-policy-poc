"""v3 Gate: 정책 엔진 없이, effect 호출을 실행 직전에 판정 LLM에 보내고 그 답(ALLOW / BLOCK / HUMAN→BLOCK)을 집행한다.

읽기 호출은 판정 없이 실행한다. 실행된 툴 응답마다 ToolSpec(JSON)을 붙여 다음 판정의 입력으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable

from agentgate.gate.base import ToolCall, Verdict
from agentgate.gate.engine import AllowAllPolicyEngine, EngineResult
from agentgate.gate.runtime import GatedRuntime, TraceEvent
from agentgate.v3 import effect_tools


class ToolSpecGatedRuntime(GatedRuntime):
    """`toolspec`: (tool, args, output_text) -> validated ToolSpec dict | None (`agentgate.v3.toolspec`).
    `judge`: (call, result, context) -> judge record (`agentgate.v3.judge`)."""

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
            if self.mode == "enforce":   # an enforcing v3 Gate without a judge would allow every effect call
                raise RuntimeError(f"v3 Gate in enforce mode needs escalate_action 'judge' and a judge "
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
