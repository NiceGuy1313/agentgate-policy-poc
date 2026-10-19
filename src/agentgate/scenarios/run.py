"""Run AgentDojo scenarios with any framework under any condition (`agentgate.scenarios.runner`).

  python -m agentgate.scenarios.run --framework smolagents --condition gate_static_rules \
      --model ollama:gpt-oss:120b --suite banking --user-task user_task_0 \
      --injection-task injection_task_0 --attack tool_knowledge --out results/runs/demo
  python -m agentgate.scenarios.run --framework native --conditions no_gate gate_llm_judge gate_static_rules \
      --pairs @data/agentdojo/pairs/strat3_seed0.txt --out results/runs/<name>
  python -m agentgate.scenarios.run --agent scripted --framework mini-swe-agent --condition gate_static_rules \
      --pilot --out results/runs/scripted          # no API key: the scripted agent / fake LLM

Scenario selection
  --pairs suite:user:injection,... | @file     explicit pairs ('none' = benign); overrides the options below
  --suite S..  --user-task U..  --injection-task I..
      no --injection-task: every injection task plus a benign run, or only the benign run when --user-task is given
      --injection-task none                    benign run (may be listed with injection tasks)
  --pilot                                      banking, 4 user x 3 injection tasks
  --sample N --seed K                          N user tasks per suite

Output: <out>/<condition>/<suite>/<user_task>/<injection_task|none>.json per run, <out>/scenario_summary.json, and
<out>/run_<time>.json (arguments and status counts). Completed runs are skipped, so an interrupted run resumes.
A run whose tools bypassed the policy gate is invalid; its scores are excluded from the summary.
Caches: TaskSpec data/cache/taskspec/ (--spec-cache-dir to use another), ToolSpec data/cache/toolspec/, judge
data/cache/judge/.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

from agentgate import SUITES
from agentgate.config import load_dotenv
from agentgate.experiment.conditions import CONDITIONS
from agentgate.scenarios import runner


def build_parser(legacy: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentgate.scenarios.run", description=__doc__,
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
        args.suites, args.user_tasks, args.injection_tasks = (runner.PILOT["suites"], runner.PILOT["user_tasks"],
                                                              runner.PILOT["injection_tasks"])
    provider, agent_model = runner.set_agent_model(args.model)
    attack_model = args.attack_model_name or {"anthropic": "Claude", "openai": "GPT-4"}.get(
        provider, re.split(r"[-:_]", agent_model)[0].upper() or "AI assistant")
    if args.pairs:
        pairs = runner.parse_pairs(args.pairs)
    else:
        only_benign = not legacy and not explicit_injections and args.user_tasks and not args.pilot
        pairs = runner.select_pairs(args.suites, args.user_tasks, [] if only_benign else args.injection_tasks,
                                    benign=(not args.no_benign) and (legacy or not explicit_injections),
                                    sample=args.sample, seed=args.seed)
    jobs = runner.build_jobs(args.conditions, pairs, agent=args.agent, spec=args.spec, attack=args.attack,
                             attack_model_name=attack_model, on_block=args.on_block, out=args.out,
                             framework=args.framework, framework_options=args.framework_options,
                             model=f"{provider}:{agent_model}", max_steps=args.max_steps)
    started = time.time()
    status = runner.run_jobs(jobs, args.workers)
    print(f"{len(jobs)} jobs in {time.time() - started:.0f}s: {status}")
    runner.run_manifest(args.out, vars(args), len(jobs), status, time.time() - started)
    summary = runner.write_summary(args.out)
    for condition, metrics in summary["conditions"].items():
        print(condition, {k: metrics[k] for k in ("scored_runs", "invalid_runs", "errors", "utility",
                                                  "utility_under_attack", "asr", "blocked_calls")})
    return summary


if __name__ == "__main__":
    main()
