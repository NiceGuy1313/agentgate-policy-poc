"""어댑터 규약: 프레임워크 어댑터 기반 클래스(`FrameworkAdapter`)와 실행 결과(`FrameworkResult`).

어댑터는 번역만 하고 Gate 판정·채점은 harness가 한다. 새 프레임워크는 `FrameworkAdapter`를 상속해 `run`을 구현하고
등록한다(`agentgate.harness.registry` 내장 표, `config/frameworks.yaml`, `agentgate.frameworks` entry point 중 하나).
서버·UI·배치 스크립트·conformance 검사는 바꾸지 않는다.

의무 Gate 경로: 어댑터는 AgentDojo 툴을 `GateToolBridge.invoke`(또는 `invoke_detail`) -> `Session.invoke` ->
`PolicyGateRuntime.run_function`으로만 실행하고 `routes_tools_through_bridge = True`로 이를 선언한다. 선언이 없으면
registry와 runner가 거부하고(`contract_problems`), conformance(`python -m agentgate.harness.conformance`)가 동작을
확인하며, Gate 밖 툴 실행이 하나라도 있는 run은 무효로 보고 점수를 뺀다. 모든 어댑터는 모든 실험 조건에서 돈다.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agentgate.harness.bridge import GateToolBridge
from agentgate.harness.events import EventSink
from agentgate.harness.models import ModelSpec
from agentgate.harness.session import FRAMEWORK_CONDITIONS


@dataclass
class FrameworkResult:
    final_output: str | None
    steps: int = 0
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "calls": 0})
    error: str | None = None
    messages: list[dict] | None = None   # AgentDojo-style messages if the framework has them (native)
    info: dict = field(default_factory=dict)


_PROBES: dict[tuple, str | None] = {}


def _probe_interpreter(python: str, modules: tuple[str, ...]) -> str | None:
    """Can `python` (another venv) import agentgate and `modules`? Cached per process."""
    key = (python, modules)
    if key not in _PROBES:
        from agentgate.config import PROJECT_ROOT
        code = ("import importlib.util, sys; missing = [m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; "
                "print(','.join(missing))")
        try:
            out = subprocess.run([python, "-c", code, "agentgate", *modules], capture_output=True, text=True,
                                 timeout=30, cwd=PROJECT_ROOT)
            if out.returncode != 0:
                _PROBES[key] = f"interpreter error: {out.stderr.strip()[-200:]}"
            else:
                missing = out.stdout.strip()
                _PROBES[key] = f"not installed in {python}: {missing}" if missing else None
        except OSError as error:
            _PROBES[key] = f"interpreter {python} not found ({error.strerror})"
    return _PROBES[key]


class FrameworkAdapter:
    name = ""
    display_name = ""
    description = ""
    requires: tuple[str, ...] = ()            # importable modules; missing -> disabled in the UI
    package: str | None = None                # distribution name for the run manifest
    loggers: tuple[str, ...] = ()             # boundary-log loggers to capture
    internal_tools: frozenset[str] = frozenset()   # framework-own tools that are not AgentDojo tools
    routes_tools_through_bridge: bool = False  # the adapter's declaration of the mandatory-gate contract (above)
    conditions: tuple[str, ...] = FRAMEWORK_CONDITIONS   # informational: every adapter runs every condition
    default_max_steps = 15                    # AgentDojo's tool loop limit, for comparability
    subprocess_python: str | None = None      # set by the registry from `python:` in config/frameworks.yaml

    def contract_problems(self) -> list[str]:
        """Why this adapter breaks the adapter contract (empty when it may run)."""
        problems = []
        if self.routes_tools_through_bridge is not True:
            problems.append("does not declare routes_tools_through_bridge = True (tools must run through "
                            "GateToolBridge.invoke)")
        if type(self).run is FrameworkAdapter.run:
            problems.append("does not implement run()")
        return problems

    def unavailable_reason(self) -> str | None:
        if problems := self.contract_problems():
            return "adapter contract: " + "; ".join(problems)
        if self.subprocess_python:
            return _probe_interpreter(self.subprocess_python, self.requires)
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
