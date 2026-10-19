"""live run의 차단 무결성 검사(run 하나 단위). scripts/analysis/check_enforcement.py와 conformance 검사가 함께 쓴다.

검사 항목:
  0. 정책 Gate를 거치지 않은 툴 실행(ungated_calls)이 없는가
  1. blocked=True인 호출이 실행되지 않았는가
  2. 차단된 호출 전후로 환경이 바뀌지 않았는가 (차단 호출 직전 hash == 다음 gate event 직전 hash,
     마지막 호출이면 최종 hash)
  3. 에이전트에 반환된 툴 결과와 gate event가 1:1로 대응하는가 (같은 툴, 같은 순서). AgentDojo가 실행 전에
     거부하는 호출(알 수 없는 툴·빈 이름)은 제외하고, ToolsExecutionLoop 반복 한도에서 요청된 호출은 실행되지
     않으므로 따로 센다(`unexecuted_requests`).
"""

from __future__ import annotations

from agentgate.experiment.conditions import get_condition


def check_run(run: dict) -> list[dict]:
    issues = []
    trace = run.get("trace", [])
    for i, event in enumerate(trace):
        if event["blocked"] and event["executed"]:
            issues.append({"check": "blocked_executed", "step": i, "tool": event["tool"]})
        if event["blocked"]:
            hash_after = trace[i + 1]["env_hash_before"] if i + 1 < len(trace) else run.get("final_env_hash")
            if event.get("env_hash_before") is None or hash_after != event["env_hash_before"]:
                issues.append({"check": "env_changed_on_block", "step": i, "tool": event["tool"]})
        if event["executed"] and event.get("effective") == "BLOCK" and get_condition(run["condition"]).mode == "enforce":
            issues.append({"check": "enforce_block_executed", "step": i, "tool": event["tool"]})
    for ungated in run.get("ungated_calls") or []:   # a tool execution outside the policy gate (run is invalid)
        issues.append({"check": "ungated_execution", "tool": ungated.get("tool"), "reason": ungated.get("reason")})
    # Every tool result the agent received must come from exactly one gate event (same tool, same order).
    tool_messages = [m for m in run.get("messages", []) if m["role"] == "tool"]
    processed_tools = [m["tool"] for m in tool_messages
                       if not (m.get("error") or "").startswith(("Invalid tool", "Empty function"))]
    gate_tools = [e["tool"] for e in trace]
    if processed_tools != gate_tools:
        issues.append({"check": "gate_trace_mismatch", "tool_results": len(processed_tools),
                       "gate_events": len(gate_tools)})
    return issues


def unexecuted_requests(run: dict) -> int:
    """Calls the model requested that never got a tool result (AgentDojo loop limit)."""
    requested = sum(len(m.get("tool_calls") or []) for m in run.get("messages", []))
    return requested - sum(1 for m in run.get("messages", []) if m["role"] == "tool")
