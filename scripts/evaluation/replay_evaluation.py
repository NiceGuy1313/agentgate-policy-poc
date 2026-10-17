"""재생 평가(RQ1): AgentDojo 정답(ground truth) 궤적을 한 조건의 Gate로 재생한다. 에이전트 LLM은 없다.

사용법:
  python scripts/evaluation/replay_evaluation.py --condition C1 --spec oracle --out results/v1/replay/oracle
  python scripts/evaluation/replay_evaluation.py --condition C7 --spec llm:claude-sonnet-5-5 --attack tool_knowledge \\
      --mode enforce --out results/v3/replay/c7_llmspec_tool_knowledge

조건은 `agentgate.conditions`(C1·C2·C4 v1, C5 v2, C6·C7·C7n v3)다. audit 모드는 판정만 기록하고 판정 LLM을 부르지
않는다(ESCALATE→block으로 기록). `--escalate`로 조건의 ESCALATE 처리를 바꿀 수 있다(예: C7 --escalate block).
`--spec llm:<model>`은 data/<v1|v3>/specs/<model>/의 저장된 TaskSpec을 읽는다(v1·v2는 v1 프롬프트, v3는 v3 프롬프트).
v2 판별기: AGENTGATE_V2_DETECTOR=piguard:chunks(기본) | protectai-v2:chunks | none

출력: <out>/benign_<mode>.jsonl, <out>/attack_<mode>.jsonl, <out>/meta.json
"""

from __future__ import annotations

import argparse
import functools
import json
import multiprocessing as mp
import os
import platform
import subprocess
import time
from pathlib import Path

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.agentdojo.replay import replay_attack_pair, replay_benign_task
from agentgate.agentdojo.suites import load_suite
from agentgate.conditions import CONDITIONS, get_condition, make_runtime
from agentgate.config import PROJECT_ROOT, load_dotenv
from agentgate.gate.task_spec import load_task_spec


def _run_job(job):
    kind, suite, user_task_id, injection_task_id, spec_kind, mode, disabled, attack, condition, escalate = job
    cond = get_condition(condition)
    spec = load_task_spec(spec_kind, suite, user_task_id, version=cond.gate)
    prompt = load_suite(suite).get_user_task_by_id(user_task_id).PROMPT
    factory = functools.partial(make_runtime, condition, suite_name=suite, escalate=escalate)
    started = time.perf_counter()

    def runtime_factory(functions, *, prompt=prompt, spec, mode, disabled):
        return factory(functions, prompt=prompt, spec=spec, mode=mode, disabled=disabled)

    if kind == "benign":
        run = replay_benign_task(suite, user_task_id, spec, mode, set(disabled), runtime_factory=runtime_factory)
    else:
        run = replay_attack_pair(suite, user_task_id, injection_task_id, spec, mode, set(disabled), attack,
                                 runtime_factory=runtime_factory)
    run["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    run["spec"] = spec_kind
    run["condition"] = condition
    for event in run["trace"]:
        event["output_excerpt"] = event["output_excerpt"][:160]
    return run


def _init_worker():
    load_dotenv()
    if os.environ.get("AGENTGATE_V2_DETECTOR", "piguard").split(":")[0] != "none":
        try:
            import torch
            torch.set_num_threads(1)
        except ImportError:
            pass


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", default="C1", choices=[c for c in CONDITIONS if CONDITIONS[c].mode == "enforce"])
    parser.add_argument("--spec", default="oracle")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--mode", nargs="*", default=["audit", "enforce"], choices=["audit", "enforce"])
    parser.add_argument("--escalate", default=None, choices=["block", "allow", "judge"],
                        help="override the condition's ESCALATE handling")
    parser.add_argument("--disable", default="", help="comma separated policy ids, e.g. P1,P2")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    parser.add_argument("--limit", type=int, default=0, help="debug: max user tasks per suite")
    parser.add_argument("--pairs", default=None, help="file of 'suite user_task injection_task' lines (subset)")
    args = parser.parse_args()
    cond = get_condition(args.condition)
    pair_filter = None
    if args.pairs:
        pair_filter = {tuple(line.replace(":", " ").split()[:3]) for line in Path(args.pairs).read_text().splitlines()
                       if line.strip() and not line.startswith("#")}
    disabled = tuple(p for p in args.disable.split(",") if p)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = {mode: {"benign": [], "attack": []} for mode in args.mode}
    for suite_name in args.suites:
        suite = load_suite(suite_name)
        user_task_ids = sorted(suite.user_tasks, key=lambda x: int(x.split("_")[-1]))
        injection_task_ids = sorted(suite.injection_tasks, key=lambda x: int(x.split("_")[-1]))
        if args.limit:
            user_task_ids = user_task_ids[: args.limit]
        for mode in args.mode:
            for user_task_id in user_task_ids:
                if pair_filter is None or any(p[:2] == (suite_name, user_task_id) for p in pair_filter):
                    jobs[mode]["benign"].append(("benign", suite_name, user_task_id, None, args.spec, mode, disabled,
                                                 args.attack, args.condition, args.escalate))
                for injection_task_id in injection_task_ids:
                    if pair_filter is not None and (suite_name, user_task_id, injection_task_id) not in pair_filter:
                        continue
                    jobs[mode]["attack"].append(("attack", suite_name, user_task_id, injection_task_id, args.spec,
                                                 mode, disabled, args.attack, args.condition, args.escalate))

    started = time.time()
    with mp.Pool(args.workers, initializer=_init_worker) as pool:
        for mode in args.mode:
            for kind in ("benign", "attack"):
                path = output_dir / f"{kind}_{mode}.jsonl"
                with path.open("w") as f:
                    for run in pool.imap(_run_job, jobs[mode][kind], chunksize=4):
                        f.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
                print(f"{path}: {len(jobs[mode][kind])} runs ({time.time() - started:.0f}s)")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except Exception:
        commit = None
    from agentgate import v1, v2, v3
    config_version = {"v1": v1.load_config, "v2": v2.load_config}.get(cond.gate, lambda: v3.load_config(cond.gate))()
    meta = {"benchmark_version": BENCHMARK_VERSION, "condition": args.condition, "version": cond.version,
            "gate": cond.gate, "escalate": args.escalate or cond.escalate, "spec": args.spec, "suites": args.suites,
            "modes": args.mode, "disabled": list(disabled), "attack": args.attack,
            "policy_config_version": config_version["version"], "pairs": args.pairs,
            "v2_detector": (os.environ.get("AGENTGATE_V2_DETECTOR") or "piguard:chunks") if cond.gate == "v2" else None,
            "git_commit": commit, "python": platform.python_version(), "elapsed_s": round(time.time() - started, 1)}
    (output_dir / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
