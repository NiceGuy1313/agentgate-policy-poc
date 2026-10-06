"""Stage 6 task 5: enforcement integrity over live runs.

Checks per run:
  1. no call with blocked=True was executed
  2. the environment did not change across a blocked call (hash before the blocked call == hash before
     the next gate event, or the final hash if it was the last one)
  3. tool results returned to the agent and gate events correspond 1:1 (same tool, same order),
     ignoring calls AgentDojo rejects before execution (unknown tool / empty name). Calls requested at
     the ToolsExecutionLoop iteration limit are never executed; they are counted separately.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_run(r: dict) -> list[dict]:
    issues = []
    tr = r.get("trace", [])
    for i, e in enumerate(tr):
        if e["blocked"] and e["executed"]:
            issues.append({"check": "blocked_executed", "step": i, "tool": e["tool"]})
        if e["blocked"]:
            after = tr[i + 1]["env_hash_before"] if i + 1 < len(tr) else r.get("final_env_hash")
            if e.get("env_hash_before") is None or after != e["env_hash_before"]:
                issues.append({"check": "env_changed_on_block", "step": i, "tool": e["tool"]})
        if e["executed"] and e.get("effective") == "BLOCK" and r["condition"] in ("C1", "C2", "C4"):
            issues.append({"check": "enforce_block_executed", "step": i, "tool": e["tool"]})
    # every tool result the agent received must come from exactly one gate event (same tool, same order)
    tool_msgs = [m for m in r.get("messages", []) if m["role"] == "tool"]
    processed = [m["tool"] for m in tool_msgs
                 if not (m.get("error") or "").startswith(("Invalid tool", "Empty function"))]
    gate = [e["tool"] for e in tr]
    if processed != gate:
        issues.append({"check": "gate_trace_mismatch", "tool_results": len(processed), "gate_events": len(gate)})
    return issues


def unexecuted_requests(r: dict) -> int:
    """Calls the model requested that never got a tool result (AgentDojo loop limit)."""
    req = sum(len(m.get("tool_calls") or []) for m in r.get("messages", []))
    return req - sum(1 for m in r.get("messages", []) if m["role"] == "tool")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="results/live/<ver>/<name>")
    a = ap.parse_args()
    root = ROOT / a.run
    files = sorted(root.glob("C*/*/*/*.json"))
    per_cond, violations = Counter(), []
    stats = Counter()
    for f in files:
        r = json.loads(f.read_text())
        if r.get("error"):
            stats["errored_runs"] += 1
            continue
        per_cond[r["condition"]] += 1
        stats["runs"] += 1
        stats["gate_events"] += len(r["trace"])
        stats["blocked_events"] += sum(e["blocked"] for e in r["trace"])
        n_unexec = unexecuted_requests(r)
        stats["runs_with_unexecuted_requests_at_loop_limit"] += n_unexec > 0
        for i in check_run(r):
            violations.append({"file": str(f.relative_to(ROOT)), **i})
    out = {"runs_by_condition": dict(per_cond), **stats, "violations": len(violations),
           "violations_by_check": dict(Counter(v["check"] for v in violations)), "examples": violations[:50]}
    (root / "enforcement_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "examples"}, indent=1))


if __name__ == "__main__":
    main()
