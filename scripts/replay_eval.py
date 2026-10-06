"""Stage 5: replay AgentDojo ground-truth trajectories through the gate. No LLM calls.

  python scripts/replay_eval.py --spec oracle --mode audit enforce --out results/replay/v1/oracle

Writes <out>/benign_<mode>.jsonl, <out>/attack_<mode>.jsonl and <out>/meta.json.
`--spec llm:<model>` reads Stage 4 extractions from data/specs/llm/<model>/ (never calls the LLM).
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
from agentgate.config import policies_config
from agentgate.replay import run_attack, run_benign
from agentgate.spec import load_spec
from agentgate.suites import load_suite

ROOT = Path(__file__).resolve().parents[1]


def _job(job):
    kind, suite, ut, it, spec_kind, mode, disabled, attack = job
    spec = load_spec(spec_kind, suite, ut)
    t0 = time.perf_counter()
    if kind == "benign":
        r = run_benign(suite, ut, spec, mode, set(disabled))
    else:
        r = run_attack(suite, ut, it, spec, mode, set(disabled), attack)
    r["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    r["spec"] = spec_kind
    for e in r["trace"]:
        e["output_excerpt"] = e["output_excerpt"][:160]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="oracle")
    ap.add_argument("--suites", nargs="*", default=list(SUITES))
    ap.add_argument("--mode", nargs="*", default=["audit", "enforce"], choices=["audit", "enforce"])
    ap.add_argument("--disable", default="", help="comma separated policy ids, e.g. P1,P2")
    ap.add_argument("--attack", default="important_instructions")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    ap.add_argument("--limit", type=int, default=0, help="debug: max user tasks per suite")
    args = ap.parse_args()
    disabled = tuple(p for p in args.disable.split(",") if p)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    jobs = {m: {"benign": [], "attack": []} for m in args.mode}
    for s in args.suites:
        suite = load_suite(s)
        uts = sorted(suite.user_tasks, key=lambda x: int(x.split("_")[-1]))
        its = sorted(suite.injection_tasks, key=lambda x: int(x.split("_")[-1]))
        if args.limit:
            uts = uts[: args.limit]
        for m in args.mode:
            for ut in uts:
                jobs[m]["benign"].append(("benign", s, ut, None, args.spec, m, disabled, args.attack))
                for it in its:
                    jobs[m]["attack"].append(("attack", s, ut, it, args.spec, m, disabled, args.attack))

    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        for m in args.mode:
            for kind in ("benign", "attack"):
                path = out / f"{kind}_{m}.jsonl"
                with path.open("w") as f:
                    for r in pool.imap(_job, jobs[m][kind], chunksize=4):
                        f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                print(f"{path}: {len(jobs[m][kind])} runs ({time.time() - t0:.0f}s)")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = None
    meta = {"benchmark_version": BENCHMARK_VERSION, "spec": args.spec, "suites": args.suites, "modes": args.mode,
            "disabled": list(disabled), "attack": args.attack, "policy_config_version": policies_config()["version"],
            "git_commit": commit, "python": platform.python_version(), "elapsed_s": round(time.time() - t0, 1)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
