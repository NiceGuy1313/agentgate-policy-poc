"""Enforcement integrity over live runs.

Checks per run:
  1. no call with blocked=True was executed
  2. the environment did not change across a blocked call (hash before the blocked call == hash before
     the next gate event, or the final hash if it was the last one)
  3. tool results returned to the agent and gate events correspond 1:1 (same tool, same order),
     ignoring calls AgentDojo rejects before execution (unknown tool / empty name). Calls requested at
     the ToolsExecutionLoop iteration limit are never executed; they are counted separately.

Usage: python scripts/analysis/check_enforcement.py --run results/live/v1/<name>
Writes <run>/enforcement_check.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agentgate.config import PROJECT_ROOT
from agentgate.evaluation.live_run_analysis import live_run_files


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
        if event["executed"] and event.get("effective") == "BLOCK" and run["condition"] in ("C1", "C2", "C4"):
            issues.append({"check": "enforce_block_executed", "step": i, "tool": event["tool"]})
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="results/live/<ver>/<name>")
    args = parser.parse_args()
    run_dir = PROJECT_ROOT / args.run
    files = live_run_files(run_dir)
    runs_by_condition, violations = Counter(), []
    stats = Counter()
    for path in files:
        run = json.loads(path.read_text())
        if run.get("error"):
            stats["errored_runs"] += 1
            continue
        runs_by_condition[run["condition"]] += 1
        stats["runs"] += 1
        stats["gate_events"] += len(run["trace"])
        stats["blocked_events"] += sum(e["blocked"] for e in run["trace"])
        unexecuted_count = unexecuted_requests(run)
        stats["runs_with_unexecuted_requests_at_loop_limit"] += unexecuted_count > 0
        for issue in check_run(run):
            violations.append({"file": str(path.relative_to(PROJECT_ROOT)), **issue})
    result = {"runs_by_condition": dict(runs_by_condition), **stats, "violations": len(violations),
              "violations_by_check": dict(Counter(v["check"] for v in violations)), "examples": violations[:50]}
    (run_dir / "enforcement_check.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}, indent=1))


if __name__ == "__main__":
    main()
