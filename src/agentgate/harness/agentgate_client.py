"""agentgate Wrapper client, contract v1. Standard library only: copy this file into any Wrapper.

Environment (set by agentgate when it launches a Wrapper):
  AGENTGATE_URL        e.g. http://127.0.0.1:8000
  AGENTGATE_SESSION    session id
  AGENTGATE_TOKEN      bearer token of the session
  AGENTGATE_MODEL      JSON: {"provider", "model", "base_url", "api_key", "litellm": {...}, "openai": {...}}
  AGENTGATE_MAX_STEPS  step limit
  AGENTGATE_OPTIONS    JSON of framework options

Usage in a Wrapper's tool execution point:
    gate = GateSession.from_env()
    task, tools = gate.prompt(), gate.tools()          # register `tools` as the agent's tools
    text = gate.invoke(name, args)                     # instead of executing the tool
    gate.event("llm_query", "...", step=1, data={...}) # optional: mirror boundary logs to the dashboard
    gate.finish(final_answer)
Fail-closed: if the gate cannot be reached, `invoke` returns a block message (the call is never executed).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CONTRACT_VERSION = "v1"
UNREACHABLE = "Tool call blocked by security policy GATE: agentgate is unreachable ({error}); the call was not executed."


class GateError(RuntimeError):
    pass


class GateSession:
    def __init__(self, url: str, session_id: str, token: str, timeout: float = 120.0):
        self.url, self.session_id, self.token, self.timeout = url.rstrip("/"), session_id, token, timeout

    @classmethod
    def from_env(cls) -> "GateSession":
        try:
            return cls(os.environ["AGENTGATE_URL"], os.environ["AGENTGATE_SESSION"], os.environ["AGENTGATE_TOKEN"])
        except KeyError as missing:
            raise GateError(f"{missing} is not set: start this Wrapper from agentgate or call GateSession.create")

    @classmethod
    def create(cls, url: str, scenario: dict, framework: str = "external", model: dict | None = None) -> "GateSession":
        """Open a session yourself (e.g. from a Wrapper's own dashboard). scenario: {suite, user_task, ...}."""
        body = _request("POST", f"{url.rstrip('/')}/{CONTRACT_VERSION}/sessions", None,
                        {"scenario": scenario, "framework": framework, "model": model or {}}, 120.0)
        return cls(url, body["session_id"], body["token"])

    @staticmethod
    def model_from_env() -> dict:
        return json.loads(os.environ.get("AGENTGATE_MODEL") or "{}")

    @staticmethod
    def max_steps_from_env(default: int = 15) -> int:
        return int(os.environ.get("AGENTGATE_MAX_STEPS") or default)

    def _call(self, method: str, path: str, body: dict | None = None):
        return _request(method, f"{self.url}/{CONTRACT_VERSION}/sessions/{self.session_id}{path}", self.token, body,
                        self.timeout)

    def info(self) -> dict:
        return self._call("GET", "")

    def prompt(self) -> str:
        return self.info()["prompt"]

    def tools(self) -> list[dict]:
        """[{name, description, parameters (JSON schema)}]"""
        return self._call("GET", "/tools")["tools"]

    def openai_tools(self) -> list[dict]:
        return [{"type": "function", "function": tool} for tool in self.tools()]

    def invoke(self, tool: str, args: dict | None = None) -> str:
        try:
            return self._call("POST", "/invoke", {"tool": tool, "args": args or {}})["output"]
        except Exception as error:   # fail closed
            return UNREACHABLE.format(error=f"{type(error).__name__}: {error}")

    def invoke_detail(self, tool: str, args: dict | None = None) -> dict:
        """Like invoke, with the gate decision: {output, blocked, verdict, effective, policy_id, reason}."""
        try:
            return self._call("POST", "/invoke", {"tool": tool, "args": args or {}})
        except Exception as error:
            return {"output": UNREACHABLE.format(error=f"{type(error).__name__}: {error}"), "blocked": True,
                    "verdict": "BLOCK", "effective": "BLOCK", "policy_id": "GATE", "reason": "unreachable"}

    def decide(self, tool: str, args: dict | None = None) -> dict:
        """Decision only, for the Wrapper's own (non-AgentDojo) tools. Fail closed on errors."""
        try:
            return self._call("POST", "/decide", {"tool": tool, "args": args or {}})
        except Exception as error:
            return {"verdict": "BLOCK", "decisions": [], "error": f"{type(error).__name__}: {error}"}

    def event(self, kind: str, message: str = "", step: int | None = None, data: dict | None = None,
              level: str = "INFO") -> None:
        try:
            self._call("POST", "/events", {"events": [{"kind": kind, "message": message, "step": step,
                                                       "data": data or {}, "level": level}]})
        except Exception:
            pass   # events are best effort

    def finish(self, final_output: str | None) -> dict:
        return self._call("POST", "/finish", {"final_output": final_output})


def _request(method: str, url: str, token: str | None, body: dict | None, timeout: float):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise GateError(f"{error.code} {error.read().decode(errors='replace')[:500]}") from None
