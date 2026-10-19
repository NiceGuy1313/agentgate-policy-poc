"""게이트의 판정을 호출 단위로 출력한다.

사용법:
  # (user task, injection task) 쌍 하나를 게이트로 재생하며 모든 판정 출력
  python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 [--mode enforce] [--spec oracle] \
      [--condition gate_static_rules]
  # benign task (injection 없음)
  python scripts/analysis/inspect_trace.py benign workspace user_task_25
  # 저장된 live run 하나
  python scripts/analysis/inspect_trace.py live results/live/<run>/gate_static_rules/banking/user_task_0/injection_task_7.json
  # 가상의 단일 호출에 대한 룰북 정적 검사(gate_static_rules) 판정. --read 응답에는 ToolSpec이 없다
  python scripts/analysis/inspect_trace.py call --prompt "Pay the bill" --spec-yaml data/agentdojo/oracle_specs/banking/user_task_0.yaml \\
      --tool send_money --args '{"recipient": "<IBAN>", "amount": 10, "subject": "x", "date": "d"}'
"""

from __future__ import annotations

import argparse
import json
import textwrap

from agentgate.core.task_spec import TaskSpec
from agentgate.core.types import ToolCall
from agentgate.experiment.conditions import CONDITIONS, make_runtime
from agentgate.policy_gate.rule_checker import RuleChecker
from agentgate.policy_gate.runtime import PolicyGateRuntime
from agentgate.scenarios.replay import replay_attack_pair, replay_benign_task
from agentgate.scenarios.task_specs import load_task_spec

VERDICT_MARKERS = {"ALLOW": "  ", "BLOCK": "X "}


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
            judge = event["judge"]
            print(f"       judge {judge.get('judge')}: {judge.get('verdict')} {judge.get('rule_ids') or ''} "
                  f"{textwrap.shorten(judge.get('reason') or judge.get('text') or '', 160)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    pair_parser = subparsers.add_parser("pair")
    pair_parser.add_argument("suite"), pair_parser.add_argument("user_task"), pair_parser.add_argument("injection_task")
    pair_parser.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    pair_parser.add_argument("--spec", default="oracle")
    pair_parser.add_argument("--attack", default="important_instructions")
    pair_parser.add_argument("--condition", default="gate_static_rules", choices=list(CONDITIONS))
    benign_parser = subparsers.add_parser("benign")
    benign_parser.add_argument("suite"), benign_parser.add_argument("user_task")
    benign_parser.add_argument("--mode", default="audit", choices=["audit", "enforce"])
    benign_parser.add_argument("--spec", default="oracle")
    benign_parser.add_argument("--condition", default="gate_static_rules", choices=list(CONDITIONS))
    live_parser = subparsers.add_parser("live")
    live_parser.add_argument("path")
    call_parser = subparsers.add_parser("call")
    call_parser.add_argument("--prompt", required=True)
    call_parser.add_argument("--spec-yaml", default=None)
    call_parser.add_argument("--tool", required=True)
    call_parser.add_argument("--args", required=True)
    call_parser.add_argument("--read", action="append", default=[], help="tool=text : a prior tool response, repeatable")
    args = parser.parse_args()

    def factory(functions, *, prompt, spec, mode):
        return make_runtime(args.condition, functions, suite_name=args.suite, prompt=prompt, spec=spec, mode=mode)

    if args.cmd == "pair":
        run = replay_attack_pair(args.suite, args.user_task, args.injection_task,
                                 load_task_spec(args.spec, args.suite, args.user_task), args.mode,
                                 attack_name=args.attack, runtime_factory=factory)
        show(run["trace"], f"{args.suite}/{args.user_task} + {args.injection_task} [{args.mode}, spec={args.spec}] "
                           f"utility={run['utility']} attack_success={run['security']}")
    elif args.cmd == "benign":
        run = replay_benign_task(args.suite, args.user_task, load_task_spec(args.spec, args.suite, args.user_task),
                                 args.mode, runtime_factory=factory)
        show(run["trace"], f"{args.suite}/{args.user_task} [{args.mode}, spec={args.spec}] utility={run['utility']}")
    elif args.cmd == "live":
        run = json.load(open(args.path))
        show(run["trace"], f"{run['condition']} {run['suite']}/{run['user_task']} + {run['injection_task']} "
                           f"utility={run.get('utility')} attack_success={run.get('security')}")
        print("\nfinal answer:", textwrap.shorten(run.get("final_output") or "", 300))
    else:
        spec = TaskSpec.load(args.spec_yaml) if args.spec_yaml else TaskSpec()
        runtime = PolicyGateRuntime((), prompt=args.prompt, spec=spec)
        for i, read in enumerate(args.read):
            tool, text = read.split("=", 1)
            runtime.index.add_response(i, tool, {}, text, text)
        call_args = json.loads(args.args)
        judgement = RuleChecker()(ToolCall(args.tool, call_args), runtime.context)
        show([{"step": 0, "tool": args.tool, "args": call_args, "verdict": judgement["verdict"],
               "effective": judgement["verdict"], "judge": judgement}],
             "single call")


if __name__ == "__main__":
    main()
