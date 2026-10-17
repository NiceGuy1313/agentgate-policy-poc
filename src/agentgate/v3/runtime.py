"""v3 Gate: 툴 응답마다 ToolSpec(JSON)을 붙이고, 위임 자료를 지목 자료로 함께 센다."""

from __future__ import annotations

from collections.abc import Callable

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


class ToolSpecGatedRuntime(GatedRuntime):
    """`toolspec`: (tool, args, output_text) -> validated ToolSpec dict | None (`agentgate.v3.toolspec`)."""

    index_class = ToolSpecValueIndex

    def __init__(self, functions=(), *, toolspec: Callable[[str, dict, str], dict | None] | None = None, **kwargs):
        super().__init__(functions, **kwargs)
        self.toolspec = toolspec
        self.trace_label_keys = ("toolspec",) if toolspec is not None else ()

    def label_response(self, source, event: TraceEvent) -> None:
        if self.toolspec is None:
            return
        spec = self.toolspec(event.tool, event.args, event.output_text)
        source.labels["toolspec"] = spec
        event.labels["toolspec"] = {"ok": spec is not None, "records": len((spec or {}).get("records", [])),
                                    "requests": len((spec or {}).get("requests", [])), "named": source.is_named,
                                    "delegated": source.labels["delegated"]}
