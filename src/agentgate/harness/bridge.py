"""The only door an agent framework uses to reach AgentDojo tools: every call goes through the gate."""

from __future__ import annotations

import json

from agentgate.harness.events import EventSink, preview
from agentgate.harness.scenario import Session


def _jsonable(args: dict | None) -> dict | None:
    """Arguments as JSON data for the dashboard's JSON viewer; None when they do not serialize or are large."""
    try:
        return args if len(json.dumps(args)) <= 4000 else None
    except (TypeError, ValueError):
        return None


class GateToolBridge:
    def __init__(self, session: Session, sink: EventSink):
        self.session = session
        self.sink = sink
        self.calls: list[dict] = []   # what the agent actually received, in order

    def tool_specs(self) -> list[dict]:
        return self.session.tool_specs()

    @property
    def tool_names(self) -> set[str]:
        return {spec["name"] for spec in self.tool_specs()}

    def invoke(self, tool: str, args: dict | None) -> str:
        text, record = self.session.invoke(tool, args or {})
        self.observe(tool, args, text, record)
        return text

    def observe(self, tool: str, args: dict | None, text: str, record: dict) -> None:
        """Publish a gate decision and remember what the agent received."""
        primary = next((d for d in record.get("decisions", []) if d.get("verdict") == record.get("verdict")), None)
        self.sink.emit(
            "gate_decision", f"{record.get('effective') or '?'} {tool}",
            level="WARNING" if record.get("blocked") else "INFO",
            data={"tool": tool, "args": preview(args, 600), "args_value": _jsonable(args), "verdict": record.get("verdict"),
                  "effective": record.get("effective"), "blocked": record.get("blocked"),
                  "executed": record.get("executed"), "error": record.get("error"),
                  "policy_id": primary.get("policy_id") if primary else None,
                  "reason": primary.get("reason") if primary else None,
                  "decisions": record.get("decisions"), "judge": record.get("judge"),
                  "decide_ms": record.get("decide_ms"), "output": preview(text, 1200)})
        self.calls.append({"tool": tool, "args": dict(args or {}), "text": text,
                           "error": record.get("error") or (text if record.get("blocked") else None)})

    def decide(self, tool: str, args: dict | None) -> dict:
        decision = self.session.decide(tool, args or {})
        self.sink.emit("gate_decision", f"{decision.get('verdict')} {tool} (decide only)",
                       data={"tool": tool, "args": preview(args, 600), "decide_only": True, **decision})
        return decision

    def messages(self, final_output: str | None) -> list[dict]:
        """AgentDojo-style message list rebuilt from the calls (check_enforcement.py compares it with the trace)."""
        messages: list[dict] = []
        for call in self.calls:
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"function": call["tool"], "args": call["args"]}]})
            messages.append({"role": "tool", "content": (call["text"] or "")[:1500], "error": call["error"],
                             "tool": call["tool"]})
        messages.append({"role": "assistant", "content": (final_output or "")[:1500]})
        return messages

    def ungated_calls(self, internal_tools: set[str] = frozenset()) -> list[dict]:
        """Tool executions the framework logged that never reached the gate (bypass audit).

        Uses the framework's own `tool_start` boundary logs: a suite tool started more often than the gate saw
        it, or a tool that is neither a suite tool nor a declared framework-internal tool, was not gated.
        """
        names = self.tool_names
        started = [e.data.get("action") for e in self.sink.events if e.kind == "tool_start"]
        issues = [{"tool": name, "reason": "not a gated suite tool"} for name in started
                  if name not in names and name not in internal_tools]
        gated = [call["tool"] for call in self.calls]
        for name in sorted(set(started) & names):
            extra = started.count(name) - gated.count(name)
            if extra > 0:
                issues.append({"tool": name, "reason": f"{extra} execution(s) without a gate decision"})
        return issues
