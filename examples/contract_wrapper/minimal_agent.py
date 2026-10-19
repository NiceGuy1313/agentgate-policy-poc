"""contract v1 참조 Wrapper: 표준 라이브러리만 쓰는 최소 OpenAI 호환 tool-calling 에이전트.

Wrapper가 구현할 규약 다섯 가지를 보여 준다.
  1. 툴 주입     gate.tools() -> 에이전트 툴 목록
  2. 실행 hook   툴 실행 지점에서 gate.invoke(name, args) (차단 메시지도 툴 결과로 돌아온다)
  3. 모델        AGENTGATE_MODEL(OpenAI 호환 base_url + 키), 제공자를 고정하지 않는다
  4. 진입점      agentgate가 AGENTGATE_* 환경 변수로 실행한다
  5. 이벤트      gate.event(...)로 경계 로그(llm_query, llm_response, tool_start, tool_end)를 보낸다
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

try:
    from agentgate.harness.agentgate_client import GateSession
except ImportError:   # a Wrapper that copied agentgate_client.py next to itself
    sys.path.insert(0, str(Path(__file__).parent))
    from agentgate_client import GateSession


def chat(model: dict, messages: list, tools: list) -> dict:
    body = {"model": model["model"], "messages": messages, "tools": tools}
    if model.get("temperature") is not None:
        body["temperature"] = model["temperature"]
    request = urllib.request.Request(f"{model['openai']['base_url'].rstrip('/')}/chat/completions",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {model['openai']['api_key']}"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read())


def main() -> None:
    gate = GateSession.from_env()
    model, max_steps = GateSession.model_from_env(), GateSession.max_steps_from_env()
    tools = gate.openai_tools()
    messages = [{"role": "system", "content": "You are a helpful assistant. Use the tools to complete the task."},
                {"role": "user", "content": gate.prompt()}]
    final = None
    for step in range(1, max_steps + 1):
        gate.event("llm_query", f"{model['model']} · {len(messages)} messages", step=step,
                   data={"model": model["model"], "messages": len(messages)})
        started = time.perf_counter()
        reply = chat(model, messages, tools)["choices"][0]["message"]
        calls = reply.get("tool_calls") or []
        gate.event("llm_response", ", ".join(c["function"]["name"] for c in calls) or "answer", step=step,
                   data={"actions": [{c["function"]["name"]: c["function"]["arguments"]} for c in calls],
                         "duration_seconds": round(time.perf_counter() - started, 3)})
        messages.append({"role": "assistant", "content": reply.get("content"), "tool_calls": calls or None})
        if not calls:
            final = reply.get("content") or ""
            break
        for call in calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            gate.event("tool_start", name, step=step, data={"action": name, "params": json.dumps(args)})
            output = gate.invoke(name, args)                         # <- the execution hook
            gate.event("tool_end", name, step=step, data={"action": name, "result_type": "str"})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
    gate.finish(final)


if __name__ == "__main__":
    main()
