"""LLM 없는 OpenAI 호환 chat endpoint: 대본 궤적으로 임의의 프레임워크를 구동한다(conformance용).

`agentgate.agents.scripted_agent.ScriptedAgent`와 같은 역할을 모델 API 수준에서 해, 프레임워크의 실제 모델 경로
(LiteLLM, OpenAI SDK, Wrapper 자체 client)를 지나게 한다.
  - 사용자 작업의 ground truth를 따르고 응답마다 툴 호출 하나를 낸다.
  - 대화 속 툴 결과에 인젝션 표지가 나오면 injection task의 호출(session의 현재 환경으로 계획, `plan_attack_calls`)을
    끼워 넣은 뒤 이어 간다.
  - 정책 차단 뒤에도 계속하고, 끝에 ground truth 출력으로 답한다.
요청에 `final_answer` 툴이 있으면(smolagents) 그 툴 호출로 답한다. 툴이 `bash` 하나뿐인 요청(mini-swe-agent)에는 gated
shell 명령을 준다.
"""

from __future__ import annotations

import json
import shlex
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agentgate.core.runtime import to_plain_data
from agentgate.scenarios.replay import ATTACK_MARKER


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return "" if content is None else str(content)


class ScriptedPlan:
    """대본 궤적: 사용자 작업의 ground truth를 따르다 인젝션 표지가 보이면 공격 호출을 끼워 넣는다.

    `on_block="stop"`이면 Gate가 호출을 하나라도 차단한 뒤에는 남은 계획을 버리고 최종 답으로 끝낸다
    (`ScriptedAgent`의 같은 이름 옵션과 같다)."""

    def __init__(self, session, marker: str = ATTACK_MARKER, on_block: str = "continue"):
        self.session, self.marker, self.on_block = session, marker, on_block
        self.plan: list | None = None
        self.hijacked = False
        self.requests = 0

    def next(self, messages: list[dict]) -> tuple[str, str, dict | str]:
        self.requests += 1
        session, scenario = self.session, self.session.scenario
        if self.plan is None:
            self.plan = scenario.ground_truth_calls()
        seen = " ".join(_text(m.get("content")) for m in messages if m.get("role") != "system")
        if session.injection_task is not None and not self.hijacked and self.marker in seen:
            self.hijacked = True
            self.plan = scenario.attack_calls() + self.plan
        stopped = self.on_block == "stop" and any(event.blocked for event in session.runtime.trace)
        if self.plan and not stopped:
            call = self.plan.pop(0)
            return "call", call.function, to_plain_data(dict(call.args))
        return "final", "", scenario.ground_truth_output(self.hijacked) or "Done."


class FakeLLMServer:
    """`with FakeLLMServer(plan) as server: ... server.base_url` (OpenAI-compatible, ends with /v1)."""

    def __init__(self, plan: ScriptedPlan, model: str = "scripted"):
        self.plan, self.model = plan, model
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = {"object": "list", "data": [{"id": outer.model, "object": "model"}]}
                self._send(200, body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.rstrip("/").endswith("chat/completions"):
                    self._send(404, {"error": {"message": f"unknown path {self.path}"}})
                    return
                self._send(200, outer.complete(request))

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def complete(self, request: dict) -> dict:
        with self.lock:
            kind, name, payload = self.plan.next(request.get("messages") or [])
        tool_names = {t.get("function", {}).get("name") for t in request.get("tools") or []}
        message: dict = {"role": "assistant", "content": None}
        if kind == "final" and "final_answer" in tool_names:
            kind, name, payload = "call", "final_answer", {"answer": payload}
        if tool_names == {"bash"}:   # mini-swe-agent: tools are commands of the gated shell
            command = f"{name} {shlex.quote(json.dumps(payload))}" if kind == "call" else f"submit {shlex.quote(payload)}"
            kind, name, payload = "call", "bash", {"command": command}
        if kind == "call":
            message["tool_calls"] = [{"id": f"call_{self.plan.requests}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(payload)}}]
            finish = "tool_calls"
        else:
            message["content"] = payload
            finish = "stop"
        return {"id": f"chatcmpl-{self.plan.requests}", "object": "chat.completion", "created": int(time.time()),
                "model": request.get("model", self.model),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
