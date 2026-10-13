"""Adapter contract: a framework adapter translates, the harness gates and scores.

To add a framework: subclass `FrameworkAdapter`, implement `run`, and register it (built-in table in
`agentgate.harness.registry`, a `config/frameworks.yaml` entry, or an `agentgate.frameworks` entry point).
Nothing else (server, UI, batch script, conformance test) changes.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from agentgate.harness.bridge import GateToolBridge
from agentgate.harness.events import EventSink
from agentgate.harness.models import ModelSpec
from agentgate.harness.scenario import FRAMEWORK_CONDITIONS


@dataclass
class FrameworkResult:
    final_output: str | None
    steps: int = 0
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "calls": 0})
    error: str | None = None
    messages: list[dict] | None = None   # AgentDojo-style messages if the framework has them (native)
    info: dict = field(default_factory=dict)


class FrameworkAdapter:
    name = ""
    display_name = ""
    description = ""
    requires: tuple[str, ...] = ()            # importable modules; missing -> disabled in the UI
    package: str | None = None                # distribution name for the run manifest
    loggers: tuple[str, ...] = ()             # boundary-log loggers to capture
    internal_tools: frozenset[str] = frozenset()   # framework-own tools that are not AgentDojo tools
    conditions: tuple[str, ...] = FRAMEWORK_CONDITIONS
    default_max_steps = 15                    # AgentDojo's tool loop limit, for comparability

    def unavailable_reason(self) -> str | None:
        missing = [m for m in self.requires if importlib.util.find_spec(m) is None]
        return f"not installed: {', '.join(missing)}" if missing else None

    def options_schema(self) -> dict:
        """JSON schema of framework-specific options; the dashboard renders it as form fields."""
        return {"type": "object", "properties": {}}

    def architecture_html(self) -> Path | None:
        return None

    def version(self) -> str | None:
        if not self.package:
            return None
        try:
            dist = importlib.metadata.distribution(self.package)
        except importlib.metadata.PackageNotFoundError:
            return None
        direct = dist.read_text("direct_url.json") or ""
        return f"{dist.version} {direct}".strip()

    def describe(self) -> dict:
        return {"name": self.name, "display_name": self.display_name or self.name, "description": self.description,
                "unavailable": self.unavailable_reason(), "options_schema": self.options_schema(),
                "conditions": list(self.conditions), "internal_tools": sorted(self.internal_tools), "default_max_steps": self.default_max_steps,
                "has_architecture": self.architecture_html() is not None}

    def run(self, task: str, bridge: GateToolBridge, model: ModelSpec, options: dict, max_steps: int,
            sink: EventSink) -> FrameworkResult:
        raise NotImplementedError
