"""v3 Gate: 툴 응답마다 ToolSpec(JSON)을 붙이고, 위임 자료를 지목 자료로 함께 센다.

judge_scope="all"(C8)이면 ESCALATE만이 아니라 모든 effect 호출과 엔진이 ALLOW하지 않은 호출을 판정 LLM이 최종 판단한다.
엔진 판정은 판정 LLM의 근거로 넘어간다. 예외는 사용자 고정 설정 always_block(P0 BLOCK)으로, 판정 없이 BLOCK이다.
엔진이 ALLOW한 읽기 호출은 판정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable

from agentgate.config import load_config_yaml
from agentgate.gate.base import ToolCall, Verdict
from agentgate.gate.engine import EngineResult
from agentgate.gate.provenance import ValueIndex
from agentgate.gate.runtime import GatedRuntime, TraceEvent
from agentgate.v3.labels import is_delegated_source_response


class ToolSpecValueIndex(ValueIndex):
    def is_named_response(self, tool: str, args: dict) -> bool:
        return super().is_named_response(tool, args) or self.is_delegated_response(tool, args)

    def is_delegated_response(self, tool: str, args: dict) -> bool:
        return is_delegated_source_response(tool, args, self.spec, self.named_match_min_len)

    def add_response(self, step, tool, args, raw_output, output_text):
        source = super().add_response(step, tool, args, raw_output, output_text)
        source.labels["delegated"] = self.is_delegated_response(tool, args)
        return source


def effect_tools() -> set[str]:
    return {tool for tools in load_config_yaml("agentdojo/tool_roles.yaml")["effect"].values() for tool in tools}


class ToolSpecGatedRuntime(GatedRuntime):
    """`toolspec`: (tool, args, output_text) -> validated ToolSpec dict | None (`agentgate.v3.toolspec`).
    `judge_scope`: "escalate" (C6, C7, C7n) | "all" (C8)."""

    index_class = ToolSpecValueIndex

    def __init__(self, functions=(), *, toolspec: Callable[[str, dict, str], dict | None] | None = None,
                 judge_scope: str = "escalate", **kwargs):
        super().__init__(functions, **kwargs)
        if judge_scope not in ("escalate", "all"):
            raise ValueError("judge_scope must be escalate | all")
        self.toolspec = toolspec
        self.judge_scope = judge_scope
        self.effect_tools = effect_tools()
        self.trace_label_keys = ("toolspec",) if toolspec is not None else ()

    def resolve(self, call: ToolCall, result: EngineResult) -> tuple[Verdict, dict | None]:
        if self.judge_scope != "all" or self.escalate_action != "judge" or self.judge is None:
            return super().resolve(call, result)
        if any(d.policy_id == "P0" and d.verdict == Verdict.BLOCK for d in result.decisions):
            return Verdict.BLOCK, None   # user's always_block
        if result.verdict == Verdict.ALLOW and call.tool not in self.effect_tools:
            return Verdict.ALLOW, None
        judgement = self.finalize_judgement(self.judge(call, result, self.context))
        return Verdict(judgement["verdict"]), judgement

    def label_response(self, source, event: TraceEvent) -> None:
        if self.toolspec is None:
            return
        spec = self.toolspec(event.tool, event.args, event.output_text)
        source.labels["toolspec"] = spec
        event.labels["toolspec"] = {"ok": spec is not None, "records": len((spec or {}).get("records", [])),
                                    "requests": len((spec or {}).get("requests", [])), "named": source.is_named,
                                    "delegated": source.labels["delegated"]}
