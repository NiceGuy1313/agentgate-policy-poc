"""AgentDojo 시나리오를 임의의 프레임워크·조건으로 실행하는 CLI(`agentgate.experiment.batch`). 옛 이름
`python -m agentgate.experiment.cli`도 그대로 돈다.

  python -m agentgate.experiment.cli --framework smolagents --condition gate_static_rules \
      --model ollama:gpt-oss:120b --suite banking --user-task user_task_0 \
      --injection-task injection_task_0 --attack tool_knowledge --out results/runs/demo
  python -m agentgate.experiment.cli --framework native --conditions no_gate gate_llm_judge gate_static_rules \
      --pairs @data/agentdojo/pairs/strat3_seed0.txt --out results/runs/<name>
  python -m agentgate.experiment.cli --agent scripted --framework mini-swe-agent --condition gate_static_rules \
      --pilot --out results/runs/scripted          # API 키 없음: scripted agent / 가짜 LLM

시나리오 선택
  --pairs suite:user:injection,... | @file     명시한 쌍('none' = 공격 없음). 아래 옵션보다 우선한다
  --suite S..  --user-task U..  --injection-task I..
      --injection-task 없음: 모든 injection task와 공격 없는 run 하나(--user-task를 주면 공격 없는 run만)
      --injection-task none                    공격 없는 run(injection task와 함께 쓸 수 있다)
  --pilot                                      banking, 사용자 작업 4 x injection task 3
  --sample N --seed K                          suite마다 사용자 작업 N개

출력: run마다 <out>/<condition>/<suite>/<user_task>/<injection_task|none>.json, <out>/scenario_summary.json,
<out>/run_<time>.json(인자와 상태별 개수). 끝난 run은 건너뛰므로 중단된 실행을 이어 갈 수 있다.
정책 Gate를 우회한 툴 실행이 있는 run은 무효이고 점수는 요약에서 빠진다.
캐시: TaskSpec data/cache/taskspec/(--spec-cache-dir로 변경), ToolSpec data/cache/toolspec/, 판정 data/cache/judge/.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from agentgate import SUITES
from agentgate.config import load_dotenv
from agentgate.experiment import batch
from agentgate.experiment.conditions import CONDITIONS
from agentgate.harness.session import default_attack_model_name


def build_parser(legacy: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentgate.experiment.cli", description=__doc__,
        epilog="Frameworks: any name registered in agentgate.harness.registry (native, smolagents, mini-swe-agent, "
               "browser-use, config/frameworks.yaml entries).", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--framework", default="native",
                        help="agent framework (default native = AgentDojo pipeline); every condition works with each")
    parser.add_argument("--conditions", "--condition", dest="conditions", nargs="+",
                        default=["no_gate", "gate_static_rules"], choices=list(CONDITIONS))
    parser.add_argument("--model", default=None,
                        help="agent provider:model, e.g. ollama:gpt-oss:120b (default AGENTGATE_AGENT_* from .env)")
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm",
                        help="scripted = deterministic ground-truth agent / fake LLM (no API key)")
    parser.add_argument("--suites", "--suite", dest="suites", nargs="*", default=list(SUITES))
    parser.add_argument("--user-tasks", "--user-task", dest="user_tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", "--injection-task", dest="injection_tasks", nargs="*", default=None)
    parser.add_argument("--attack", default="important_instructions", help="AgentDojo attack, e.g. tool_knowledge")
    parser.add_argument("--attack-model-name", default=None, help="name used for {model} in the attack text")
    parser.add_argument("--spec", choices=["llm", "oracle"], default="llm", help="TaskSpec source of gated conditions")
    parser.add_argument("--on-block", choices=["continue", "stop"], default="continue", help="scripted agent only")
    parser.add_argument("--no-benign", action="store_true", help="skip the no-attack (utility) runs")
    parser.add_argument("--pilot", action="store_true", help="banking, 4 user x 3 injection tasks")
    parser.add_argument("--sample", type=int, default=0,
                        help="per suite, N user tasks drawn with --seed (all injection tasks for each)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pairs", default=None,
                        help="explicit pairs 'suite:user_task:injection_task,...' or @file (one per line); "
                             "injection_task 'none' = benign run (overrides suites/tasks)")
    parser.add_argument("--framework-options", default="{}", help="framework options as JSON")
    parser.add_argument("--max-steps", type=int, default=None, help="framework step limit (default 15)")
    parser.add_argument("--spec-cache-dir", default=None,
                        help="TaskSpec extraction cache dir; a new directory makes the extractor call the LLM live"
                             + (" (default <out>/taskspec_cache)" if legacy else " (default data/cache/taskspec)"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", required=True, help="output directory, e.g. results/runs/<name>")
    return parser


def main(argv: list[str] | None = None, *, legacy: bool = False) -> dict:
    """`legacy` keeps scripts/evaluation/live_evaluation.py's defaults: a fresh TaskSpec cache inside --out, and every
    injection task (plus a benign run) when --injection-tasks is not given."""
    load_dotenv()
    args = build_parser(legacy).parse_args(argv)
    if args.spec_cache_dir or legacy:
        # a fresh directory makes the extractor call the LLM live for this run
        os.environ["AGENTGATE_TASKSPEC_CACHE_DIR"] = str(Path(args.spec_cache_dir or Path(args.out) / "taskspec_cache")
                                                         .resolve())
    explicit_injections = args.injection_tasks is not None and not args.pilot
    if args.pilot:
        args.suites, args.user_tasks, args.injection_tasks = (batch.PILOT["suites"], batch.PILOT["user_tasks"],
                                                              batch.PILOT["injection_tasks"])
    provider, agent_model = batch.set_agent_model(args.model)
    attack_model = args.attack_model_name or default_attack_model_name(provider, agent_model)
    if args.pairs:
        pairs = batch.parse_pairs(args.pairs)
    else:
        only_benign = not legacy and not explicit_injections and args.user_tasks and not args.pilot
        pairs = batch.select_pairs(args.suites, args.user_tasks, [] if only_benign else args.injection_tasks,
                                    benign=(not args.no_benign) and (legacy or not explicit_injections),
                                    sample=args.sample, seed=args.seed)
    jobs = batch.build_jobs(args.conditions, pairs, agent=args.agent, spec=args.spec, attack=args.attack,
                             attack_model_name=attack_model, on_block=args.on_block, out=args.out,
                             framework=args.framework, framework_options=args.framework_options,
                             model=f"{provider}:{agent_model}", max_steps=args.max_steps)
    started = time.time()
    status = batch.run_jobs(jobs, args.workers)
    print(f"{len(jobs)} jobs in {time.time() - started:.0f}s: {status}")
    batch.run_manifest(args.out, vars(args), len(jobs), status, time.time() - started)
    summary = batch.write_summary(args.out)
    for condition, metrics in summary["conditions"].items():
        print(condition, {k: metrics[k] for k in ("scored_runs", "invalid_runs", "errors", "utility",
                                                  "utility_under_attack", "asr", "blocked_calls")})
    return summary


if __name__ == "__main__":
    main()
