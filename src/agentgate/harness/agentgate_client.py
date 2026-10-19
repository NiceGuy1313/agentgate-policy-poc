"""agentgate Wrapper client(contract v1). 표준 라이브러리만 쓰므로 이 파일을 Wrapper에 복사해 쓴다.

환경 변수(agentgate가 Wrapper를 실행할 때 지정한다)
  AGENTGATE_URL        예: http://127.0.0.1:8000
  AGENTGATE_SESSION    session id
  AGENTGATE_TOKEN      session의 bearer token
  AGENTGATE_MODEL      JSON: {"provider", "model", "base_url", "api_key", "litellm": {...}, "openai": {...}}
  AGENTGATE_MAX_STEPS  단계 상한
  AGENTGATE_OPTIONS    프레임워크 옵션 JSON

Wrapper의 툴 실행 지점에서 쓰는 법
    gate = GateSession.from_env()
    task, tools = gate.prompt(), gate.tools()          # `tools`를 에이전트 툴로 등록한다
    text = gate.invoke(name, args)                     # 툴을 실행하는 대신 부른다
    gate.event("llm_query", "...", step=1, data={...}) # 선택: 경계 로그를 대시보드에 보낸다
    gate.finish(final_answer)
fail-closed: Gate에 닿지 못하면 `invoke`는 차단 메시지를 돌려준다(호출은 실행되지 않는다).
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
