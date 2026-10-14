"""Reference Wrapper for agentgate contract v1: a minimal OpenAI-compatible tool-calling agent.

Standard library only. Shows the five contract points a framework Wrapper implements:
  1. tool injection   gate.tools() -> the agent's tool list
  2. execution hook   gate.invoke(name, args) at the tool execution point (block message comes back as the result)
  3. model            AGENTGATE_MODEL (OpenAI-compatible base_url + key), no hard-coded provider
  4. entry point      started by agentgate with AGENTGATE_* environment variables
  5. events           gate.event(...) mirrors the boundary logs (llm_query, llm_response, tool_start, tool_end)
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
