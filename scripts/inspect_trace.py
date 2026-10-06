"""Look at what the gate decided, call by call.

  # replay one (user task, injection task) pair through the gate and print every decision
  python scripts/inspect_trace.py pair banking user_task_0 injection_task_0 [--mode enforce] [--spec oracle]
  # a benign task (no injection)
  python scripts/inspect_trace.py benign workspace user_task_25
  # one saved live run
  python scripts/inspect_trace.py live results/live/v1/scripted/C1/banking/user_task_0/injection_task_7.json
  # ask the engine about a single hypothetical call
  python scripts/inspect_trace.py call --prompt "Pay the bill" --spec-yaml data/specs/oracle/banking/user_task_0.yaml \\
      --tool send_money --args '{"recipient": "US133000000121212121212", "amount": 10, "subject": "x", "date": "d"}'
"""

from __future__ import annotations

import argparse
import json
import textwrap

from agentgate.policy.base import Call, Context
from agentgate.policy.engine import PolicyEngine
from agentgate.session import SessionState
from agentgate.spec import TaskSpec, load_spec
from agentgate.trace import ValueIndex

MARK = {"ALLOW": "  ", "ESCALATE": "? ", "BLOCK": "X "}


def show(trace: list[dict], header: str):
    print(header)
    for e in trace:
        phase = e.get("phase") or ""
        tag = f"[{phase}{'/' + e['role'] if e.get('role') else ''}]" if phase else ""
        state = "BLOCKED" if e.get("blocked") else ("executed" if e.get("executed") else "")
        args = json.dumps(e["args"], ensure_ascii=False)
        print(f"{MARK.get(e.get('verdict'), '  ')}{e['step']:>2} {tag:<20} {e['tool']}({textwrap.shorten(args, 90)})"
              f"  -> {e.get('verdict')}/{e.get('effective')} {state}")
        for d in e.get("decisions", []):
            ev = d.get("evidence", {})
            org = f" origin={ev.get('origin')}@{ev.get('tool')}" if ev.get("origin") else ""
            print(f"       {d['policy_id']} {d['verdict']:<8} {d['reason']}{org}")
        if e.get("judge"):
            print(f"       judge: {e['judge'].get('answer')} -> {e['judge'].get('final')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("suite"), p.add_argument("user_task"), p.add_argument("injection_task")
    p.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    p.add_argument("--spec", default="oracle")
    p.add_argument("--attack", default="important_instructions")
    b = sub.add_parser("benign")
    b.add_argument("suite"), b.add_argument("user_task")
    b.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    b.add_argument("--spec", default="oracle")
    lv = sub.add_parser("live")
    lv.add_argument("path")
    c = sub.add_parser("call")
    c.add_argument("--prompt", required=True)
    c.add_argument("--spec-yaml", default=None)
    c.add_argument("--tool", required=True)
    c.add_argument("--args", required=True)
    c.add_argument("--read", action="append", default=[], help="tool=text : a prior tool response, repeatable")
    a = ap.parse_args()

    if a.cmd == "pair":
        from agentgate.replay import run_attack
        r = run_attack(a.suite, a.user_task, a.injection_task, load_spec(a.spec, a.suite, a.user_task), a.mode,
                       attack_name=a.attack)
        show(r["trace"], f"{a.suite}/{a.user_task} + {a.injection_task} [{a.mode}, spec={a.spec}] "
                         f"utility={r['utility']} attack_success={r['security']}")
    elif a.cmd == "benign":
        from agentgate.replay import run_benign
        r = run_benign(a.suite, a.user_task, load_spec(a.spec, a.suite, a.user_task), a.mode)
        show(r["trace"], f"{a.suite}/{a.user_task} [{a.mode}, spec={a.spec}] utility={r['utility']}")
    elif a.cmd == "live":
        r = json.load(open(a.path))
        show(r["trace"], f"{r['condition']} {r['suite']}/{r['user_task']} + {r['injection_task']} "
                         f"utility={r.get('utility')} attack_success={r.get('security')}")
        print("\nfinal answer:", textwrap.shorten(r.get("final_output") or "", 300))
    else:
        spec = TaskSpec.load(a.spec_yaml) if a.spec_yaml else TaskSpec()
        eng = PolicyEngine()
        idx = ValueIndex(a.prompt, spec, eng.config.get("self_identifiers"))
        for i, rd in enumerate(a.read):
            tool, text = rd.split("=", 1)
            idx.add_response(i, tool, {}, text, text)
        res = eng.decide(Call(a.tool, json.loads(a.args)), Context(spec, idx, SessionState(), eng.config))
        show([{"step": 0, "tool": a.tool, "args": json.loads(a.args), "verdict": res.verdict.value,
               "effective": res.verdict.value, "decisions": [d.as_dict() for d in res.decisions]}], "single call")


if __name__ == "__main__":
    main()
