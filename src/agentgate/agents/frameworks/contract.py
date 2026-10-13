"""Any Wrapper that follows contract v1: launched as a process, talks to the gate over HTTP.

Registered in config/frameworks.yaml with a `contract:` entry; no Python adapter needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agentgate.agents.frameworks.base import FrameworkAdapter, FrameworkResult
from agentgate.config import PROJECT_ROOT


class ContractAdapter(FrameworkAdapter):
    description = "Wrapper following agentgate contract v1"

    def __init__(self, name: str, contract: dict):
        self.name = self.display_name = name
        self.command = [str(part) for part in contract["command"]]
        if self.command and self.command[0] == "python":   # the interpreter running agentgate
            self.command[0] = sys.executable
        self.cwd = Path(contract.get("cwd") or PROJECT_ROOT)
        if not self.cwd.is_absolute():
            self.cwd = PROJECT_ROOT / self.cwd
        self.timeout = float(contract.get("timeout", 1800))
        self.extra_env = {k: str(v) for k, v in (contract.get("env") or {}).items()}
        self.internal_tools = frozenset(contract.get("internal_tools") or [])
        self.schema = contract.get("options_schema") or {"type": "object", "properties": {}}

    def unavailable_reason(self) -> str | None:
        return None if self.cwd.exists() else f"cwd not found: {self.cwd}"

    def options_schema(self) -> dict:
        return self.schema

    def run(self, task, bridge, model, options, max_steps, sink):
        from agentgate.harness.service import service_url, store

        session_id, token = store.add(bridge.session, bridge, sink, framework=self.name, model=model.as_dict())
        model_info = {"provider": model.provider, "model": model.model, "base_url": model.resolved_base_url(),
                      "api_key": model.api_key(), "temperature": model.temperature,
                      "litellm": model.litellm_kwargs(), "openai": model.openai_kwargs()}
        env = {**os.environ, **self.extra_env, "AGENTGATE_URL": service_url(), "AGENTGATE_SESSION": session_id,
               "AGENTGATE_TOKEN": token, "AGENTGATE_MODEL": json.dumps(model_info),
               "AGENTGATE_MAX_STEPS": str(max_steps), "AGENTGATE_OPTIONS": json.dumps(options or {})}
        started = time.perf_counter()
        try:
            process = subprocess.run(self.command, cwd=self.cwd, env=env, capture_output=True, text=True,
                                     timeout=self.timeout)
            returncode, stdout, stderr = process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired as expired:
            returncode, stdout, stderr = "timeout", expired.stdout or "", expired.stderr or ""
        entry = store.entries[session_id]
        if stderr.strip():
            sink.emit("log", stderr.strip()[-1500:], level="WARNING" if returncode == 0 else "ERROR",
                      data={"logger": f"{self.name}:stderr"})
        error = None if returncode == 0 else f"wrapper exit {returncode}"
        if not entry.finished and error is None:
            error = "wrapper exited without calling finish"
        return FrameworkResult(final_output=entry.final_output, steps=len(bridge.calls), error=error,
                               info={"returncode": returncode, "seconds": round(time.perf_counter() - started, 2),
                                     "stdout_tail": (stdout or "")[-1000:]})
