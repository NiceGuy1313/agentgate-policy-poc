"""재생 평가(RQ1): AgentDojo 정답(ground truth) 궤적을 게이트로 재생한다. LLM 호출 없음.

사용법: python scripts/evaluation/replay_evaluation.py --spec oracle --mode audit enforce --out results/replay/v1/oracle

출력: <out>/benign_<mode>.jsonl, <out>/attack_<mode>.jsonl, <out>/meta.json
`--spec llm:<model>`은 data/specs/llm/<model>/에 저장된 LLM TaskSpec 추출 결과를 읽는다(LLM을 호출하지 않는다).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import subprocess
import time
from pathlib import Path

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.config import PROJECT_ROOT, load_policy_config
from agentgate.evaluation.replay import replay_attack_pair, replay_benign_task
from agentgate.suites import load_suite
from agentgate.task_spec import load_task_spec


def _run_job(job):
    kind, suite, user_task_id, injection_task_id, spec_kind, mode, disabled, attack = job
    spec = load_task_spec(spec_kind, suite, user_task_id)
    started = time.perf_counter()
    if kind == "benign":
        run = replay_benign_task(suite, user_task_id, spec, mode, set(disabled))
    else:
        run = replay_attack_pair(suite, user_task_id, injection_task_id, spec, mode, set(disabled), attack)
    run["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    run["spec"] = spec_kind
    for event in run["trace"]:
        event["output_excerpt"] = event["output_excerpt"][:160]
    return run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="oracle")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--mode", nargs="*", default=["audit", "enforce"], choices=["audit", "enforce"])
    parser.add_argument("--disable", default="", help="comma separated policy ids, e.g. P1,P2")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    parser.add_argument("--limit", type=int, default=0, help="debug: max user tasks per suite")
    args = parser.parse_args()
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
                jobs[mode]["benign"].append(
                    ("benign", suite_name, user_task_id, None, args.spec, mode, disabled, args.attack))
                for injection_task_id in injection_task_ids:
                    jobs[mode]["attack"].append(
                        ("attack", suite_name, user_task_id, injection_task_id, args.spec, mode, disabled,
                         args.attack))

    started = time.time()
    with mp.Pool(args.workers) as pool:
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
    meta = {"benchmark_version": BENCHMARK_VERSION, "spec": args.spec, "suites": args.suites, "modes": args.mode,
            "disabled": list(disabled), "attack": args.attack,
            "policy_config_version": load_policy_config()["version"],
            "git_commit": commit, "python": platform.python_version(), "elapsed_s": round(time.time() - started, 1)}
    (output_dir / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
