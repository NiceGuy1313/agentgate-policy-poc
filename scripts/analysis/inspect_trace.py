"""Look at what the gate decided, call by call.

Usage:
  # replay one (user task, injection task) pair through the gate and print every decision
  python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 [--mode enforce] [--spec oracle]
  # a benign task (no injection)
  python scripts/analysis/inspect_trace.py benign workspace user_task_25
  # one saved live run
  python scripts/analysis/inspect_trace.py live results/live/v1/scripted/C1/banking/user_task_0/injection_task_7.json
  # ask the engine about a single hypothetical call
  python scripts/analysis/inspect_trace.py call --prompt "Pay the bill" --spec-yaml data/specs/oracle/banking/user_task_0.yaml \\
      --tool send_money --args '{"recipient": "US133000000121212121212", "amount": 10, "subject": "x", "date": "d"}'
"""

from __future__ import annotations

import argparse
import json
import textwrap

from agentgate.evaluation.replay import replay_attack_pair, replay_benign_task
from agentgate.policy.base import PolicyContext, SessionState, ToolCall
from agentgate.policy.engine import PolicyEngine
from agentgate.provenance import ValueIndex
from agentgate.task_spec import TaskSpec, load_task_spec

VERDICT_MARKERS = {"ALLOW": "  ", "ESCALATE": "? ", "BLOCK": "X "}


def show(trace: list[dict], header: str):
    print(header)
    for event in trace:
        phase = event.get("phase") or ""
        tag = f"[{phase}{'/' + event['role'] if event.get('role') else ''}]" if phase else ""
        state = "BLOCKED" if event.get("blocked") else ("executed" if event.get("executed") else "")
        args = json.dumps(event["args"], ensure_ascii=False)
        print(f"{VERDICT_MARKERS.get(event.get('verdict'), '  ')}{event['step']:>2} {tag:<20} "
              f"{event['tool']}({textwrap.shorten(args, 90)})"
              f"  -> {event.get('verdict')}/{event.get('effective')} {state}")
        for decision in event.get("decisions", []):
            evidence = decision.get("evidence", {})
            origin = f" origin={evidence.get('origin')}@{evidence.get('tool')}" if evidence.get("origin") else ""
            print(f"       {decision['policy_id']} {decision['verdict']:<8} {decision['reason']}{origin}")
        if event.get("judge"):
            print(f"       judge: {event['judge'].get('answer')} -> {event['judge'].get('final')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    pair_parser = subparsers.add_parser("pair")
    pair_parser.add_argument("suite"), pair_parser.add_argument("user_task"), pair_parser.add_argument("injection_task")
    pair_parser.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    pair_parser.add_argument("--spec", default="oracle")
    pair_parser.add_argument("--attack", default="important_instructions")
    benign_parser = subparsers.add_parser("benign")
    benign_parser.add_argument("suite"), benign_parser.add_argument("user_task")
    benign_parser.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    benign_parser.add_argument("--spec", default="oracle")
    live_parser = subparsers.add_parser("live")
    live_parser.add_argument("path")
    call_parser = subparsers.add_parser("call")
    call_parser.add_argument("--prompt", required=True)
    call_parser.add_argument("--spec-yaml", default=None)
    call_parser.add_argument("--tool", required=True)
    call_parser.add_argument("--args", required=True)
    call_parser.add_argument("--read", action="append", default=[], help="tool=text : a prior tool response, repeatable")
    args = parser.parse_args()

    if args.cmd == "pair":
        run = replay_attack_pair(args.suite, args.user_task, args.injection_task,
                                 load_task_spec(args.spec, args.suite, args.user_task), args.mode,
                                 attack_name=args.attack)
        show(run["trace"], f"{args.suite}/{args.user_task} + {args.injection_task} [{args.mode}, spec={args.spec}] "
                           f"utility={run['utility']} attack_success={run['security']}")
    elif args.cmd == "benign":
        run = replay_benign_task(args.suite, args.user_task, load_task_spec(args.spec, args.suite, args.user_task),
                                 args.mode)
        show(run["trace"], f"{args.suite}/{args.user_task} [{args.mode}, spec={args.spec}] utility={run['utility']}")
    elif args.cmd == "live":
        run = json.load(open(args.path))
        show(run["trace"], f"{run['condition']} {run['suite']}/{run['user_task']} + {run['injection_task']} "
                           f"utility={run.get('utility')} attack_success={run.get('security')}")
        print("\nfinal answer:", textwrap.shorten(run.get("final_output") or "", 300))
    else:
        spec = TaskSpec.load(args.spec_yaml) if args.spec_yaml else TaskSpec()
        engine = PolicyEngine()
        value_index = ValueIndex(args.prompt, spec, engine.config.get("self_identifiers"))
        for i, read in enumerate(args.read):
            tool, text = read.split("=", 1)
            value_index.add_response(i, tool, {}, text, text)
        result = engine.decide(ToolCall(args.tool, json.loads(args.args)),
                               PolicyContext(spec, value_index, SessionState(), engine.config))
        show([{"step": 0, "tool": args.tool, "args": json.loads(args.args), "verdict": result.verdict.value,
               "effective": result.verdict.value, "decisions": [d.as_dict() for d in result.decisions]}],
             "single call")


if __name__ == "__main__":
    main()
