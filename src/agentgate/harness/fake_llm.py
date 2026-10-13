"""LLM-free OpenAI-compatible chat endpoint that drives any framework with a scripted trajectory.

It plays the same role as `agentgate.agents.scripted_agent.ScriptedAgent`, but at the model API, so the
framework's real model path (LiteLLM, OpenAI SDK, a Wrapper's own client) is exercised:
  - follows the user task's ground truth, one tool call per response;
  - once a tool result in the conversation contains the injection marker, inserts the injection task's calls
    (planned on the session's current environment, `plan_attack_calls`), then resumes;
  - continues after a policy block; finally answers with the ground-truth output(s).
If the request offers a `final_answer` tool (smolagents), the answer is given as that tool call.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agentgate.evaluation.attack_trajectories import plan_attack_calls
from agentgate.runtime import _to_plain_data

MARKER = "<INFORMATION>"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return "" if content is None else str(content)


class ScriptedPlan:
    def __init__(self, session, marker: str = MARKER):
        self.session, self.marker = session, marker
        self.plan: list | None = None
        self.hijacked = False
        self.requests = 0

    def next(self, messages: list[dict]) -> tuple[str, str, dict | str]:
        self.requests += 1
        session = self.session
        if self.plan is None:
            self.plan = list(session.user_task.ground_truth(session.env.model_copy(deep=True)))
        seen = " ".join(_text(m.get("content")) for m in messages if m.get("role") != "system")
        if session.injection_task is not None and not self.hijacked and self.marker in seen:
            self.hijacked = True
            calls, _ = plan_attack_calls(session.config.suite, session.injection_task, session.env)
            self.plan = list(calls) + self.plan
        if self.plan:
            call = self.plan.pop(0)
            return "call", call.function, _to_plain_data(dict(call.args))
        outputs = [session.user_task.GROUND_TRUTH_OUTPUT]
        if self.hijacked:
            outputs.append(session.injection_task.GROUND_TRUTH_OUTPUT)
        return "final", "", "\n".join(o for o in outputs if o) or "Done."


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
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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
