"""Normalized run events.

A superset of the event format used by the NiceGuy1313 framework dashboards (`run`, `llm_query`, `llm_response`,
`llm_error`, `tool_start`, `tool_end`, `tool_error`, `step`, `log`) plus `gate_decision` and `result`. Those
dashboards turn boundary log records such as ``"[TOOL_EXECUTION_START] action=%s params=%s"`` into events;
`BoundaryLogHandler` does the same generically by reading the ``key=%s`` names from the format string, so a new
framework that follows the same logging convention needs no parser of its own.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
KINDS = ("run", "llm_query", "llm_response", "llm_error", "tool_start", "gate_decision", "tool_end", "tool_error",
         "step", "result", "log")
TAG_TO_KIND = {
    "[LLM_QUERY]": "llm_query",
    "[LLM_RESPONSE]": "llm_response",
    "[LLM_ERROR]": "llm_error",
    "[TOOL_EXECUTION_START]": "tool_start",
    "[TOOL_EXECUTION_END]": "tool_end",
    "[TOOL_EXECUTION_ERROR]": "tool_error",
}
PREVIEW_CHARS = 2000
_FIELD = re.compile(r"(\w+)=%[-#0 +]*\d*(?:\.\d+)?[sdifr]")


def preview(value: Any, limit: int = PREVIEW_CHARS) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class Event:
    seq: int
    kind: str
    message: str = ""
    level: str = "INFO"
    step: int | None = None
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


class EventSink:
    """Collects events in order and fans them out to subscribers. Safe to emit from any thread."""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id
        self.events: list[Event] = []
        self.current_step: int | None = None
        self._llm_started: float | None = None
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[Event], None]) -> list[Event]:
        with self._lock:
            self._subscribers.append(callback)
            return list(self.events)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def emit(self, kind: str, message: str = "", *, level: str = "INFO", step: int | None = None,
             data: dict | None = None) -> Event:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        with self._lock:
            data = dict(data or {})
            if kind == "llm_query":
                if step is not None:
                    self.current_step = step
                self._llm_started = time.monotonic()
            elif kind in ("llm_response", "llm_error") and self._llm_started is not None:
                data.setdefault("duration_seconds", round(time.monotonic() - self._llm_started, 3))
                self._llm_started = None
            if step is None and kind in ("tool_start", "gate_decision", "tool_end", "tool_error"):
                step = self.current_step
            event = Event(seq=len(self.events), kind=kind, message=message, level=level, step=step, data=data,
                          run_id=self.run_id)
            self.events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(event)
        return event

    def emit_raw(self, event: dict) -> Event:
        """Accept an already-normalized event from a Wrapper (contract v1 `events` endpoint)."""
        return self.emit(event.get("kind", "log"), event.get("message", ""), level=event.get("level", "INFO"),
                         step=event.get("step"), data=event.get("data") or {})


def parse_boundary_record(record: logging.LogRecord) -> tuple[str, int | None, str, dict] | None:
    """`[TAG] key=%s ...` log record -> (kind, step, message, data), or None when it is not a boundary record."""
    if not isinstance(record.msg, str):
        return None
    kind = next((k for tag, k in TAG_TO_KIND.items() if record.msg.startswith(tag)), None)
    if kind is None:
        return None
    args = record.args if isinstance(record.args, tuple) else ()
    data = dict(zip(_FIELD.findall(record.msg), args))
    step = data.pop("step", None)
    step = int(step) if step not in (None, "None") else None
    if "params" in data:
        data["params"] = preview(data["params"])
    if "duration_seconds" in data:
        data["duration_seconds"] = round(float(data["duration_seconds"]), 3)
    if kind == "llm_response" and isinstance(data.get("actions"), str):
        try:
            import json
            data["actions"] = json.loads(data["actions"])
        except ValueError:
            pass
    if kind == "llm_query":
        message = f"{data.get('model')} · {data.get('messages')} messages"
    elif kind == "llm_response":
        actions = data.get("actions")
        names = [next(iter(a)) for a in actions if isinstance(a, dict) and a] if isinstance(actions, list) else []
        message = ", ".join(names) or str(data.get("purpose") or "response")
    else:
        message = str(data.get("action") or data.get("error_type") or kind)
    return kind, step, message, data


CURRENT_SINK: contextvars.ContextVar[EventSink | None] = contextvars.ContextVar("agentgate_sink", default=None)


class BoundaryLogHandler(logging.Handler):
    """Routes framework boundary logs (and WARNING+ records) into the EventSink of the run that produced them.

    Loggers are process-global, so concurrent runs (dashboard comparison, batch workers) share one handler; a
    record goes to the sink in the current context (`CURRENT_SINK`, set per run and copied into framework
    worker threads that use `contextvars.copy_context`). Records from threads without that context go to the
    only active run, or are dropped when several runs are active.
    """

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.active: list[EventSink] = []

    def emit(self, record: logging.LogRecord) -> None:
        sink = CURRENT_SINK.get() or (self.active[0] if len(self.active) == 1 else None)
        if sink is None:
            return
        try:
            parsed = parse_boundary_record(record)
            if parsed is not None:
                kind, step, message, data = parsed
                sink.emit(kind, message, level=record.levelname, step=step, data=data)
            elif record.levelno >= logging.WARNING:
                sink.emit("log", record.getMessage()[:PREVIEW_CHARS], level=record.levelname,
                          data={"logger": record.name})
        except Exception:
            self.handleError(record)


_HANDLER = BoundaryLogHandler()
_LOGGER_REFS: dict[str, list] = {}   # logger name -> [refcount, previous level]
_CAPTURE_LOCK = threading.Lock()


class capture_loggers:
    """Context manager for one run: route the given loggers' boundary logs to `sink` at DEBUG level."""

    def __init__(self, sink: EventSink, names: list[str]):
        self.sink, self.names = sink, list(names)
        self.token = None

    def __enter__(self):
        self.token = CURRENT_SINK.set(self.sink)
        with _CAPTURE_LOCK:
            _HANDLER.active.append(self.sink)
            for name in self.names:
                logger = logging.getLogger(name)
                if name not in _LOGGER_REFS:
                    _LOGGER_REFS[name] = [0, logger.level]
                    logger.addHandler(_HANDLER)
                    logger.setLevel(logging.DEBUG)
                _LOGGER_REFS[name][0] += 1
        return self

    def __exit__(self, *exc):
        with _CAPTURE_LOCK:
            _HANDLER.active.remove(self.sink)
            for name in self.names:
                ref = _LOGGER_REFS[name]
                ref[0] -= 1
                if ref[0] == 0:
                    logger = logging.getLogger(name)
                    logger.removeHandler(_HANDLER)
                    logger.setLevel(ref[1])
                    del _LOGGER_REFS[name]
        CURRENT_SINK.reset(self.token)
        return False
