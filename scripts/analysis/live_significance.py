"""Confidence intervals and paired tests for a live run, against C0.

For each condition and metric (ASR, Utility, Utility under attack):
  - rate with a Wilson 95% interval,
  - paired difference to C0 on the same (suite, user task, injection task) with the exact McNemar p-value,
  - 95% interval of the difference from a cluster bootstrap over user tasks (stratified by suite), because
    the runs of one user task are not independent.
Also counts gate-attributable failures: runs that had a blocked call and failed the user task
(and the subset that succeeded under C0), with the blocking policies.

Usage: python scripts/analysis/live_significance.py --run results/live/v1/<name>
Writes <run>/significance.json. Runs with an error are dropped (pairwise for the paired tests).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from agentgate.config import PROJECT_ROOT
from agentgate.evaluation.metrics import mcnemar_exact, wilson

# Metric name -> (run kind, record field).
METRICS = {"asr": ("attack", "security"), "utility": ("benign", "utility"),
           "utility_under_attack": ("attack", "utility")}


def load_runs(run_dir: Path) -> dict:
    runs = {}
    for path in run_dir.glob("C*/*/*/*.json"):
        run = json.loads(path.read_text())
        if run.get("error"):
            continue
        runs[(run["condition"], run["suite"], run["user_task"], run["injection_task"])] = run
    return runs


def blocked_decisions(run: dict) -> list[list]:
    """[policy, verdict, origin] of every non-ALLOW decision on a blocked call."""
    return [[d["policy_id"], d["verdict"], (d.get("evidence") or {}).get("origin")]
            for e in run["trace"] if e["blocked"] for d in e["decisions"] if d["verdict"] != "ALLOW"]


def blocked_policies(run: dict) -> list[str]:
    return sorted({d["policy_id"] for e in run["trace"] if e["blocked"] for d in e["decisions"]
                   if d["verdict"] != "ALLOW"})


def cluster_bootstrap_diff(pairs: list[tuple[tuple, int, int]], n_boot: int, seed: int) -> tuple[float, float] | None:
    """pairs: (key, x_C0, x_C). Resample user tasks with replacement within each suite."""
    if not pairs:
        return None
    diffs_by_task = defaultdict(list)
    for key, x_c0, x_condition in pairs:
        diffs_by_task[(key[0], key[1])].append(x_condition - x_c0)
    tasks_by_suite = defaultdict(list)
    for task in diffs_by_task:
        tasks_by_suite[task[0]].append(task)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        total = count = 0
        for tasks in tasks_by_suite.values():
            for _ in range(len(tasks)):
                task_diffs = diffs_by_task[tasks[rng.randrange(len(tasks))]]
                total += sum(task_diffs)
                count += len(task_diffs)
        diffs.append(total / count)
    diffs.sort()
    return round(diffs[int(0.025 * n_boot)], 4), round(diffs[int(0.975 * n_boot) - 1], 4)


def analyse(run_dir: Path, n_boot: int = 2000, seed: int = 0) -> dict:
    runs = load_runs(run_dir)
    conditions = sorted({key[0] for key in runs})
    result = {"run": str(run_dir.relative_to(PROJECT_ROOT)) if run_dir.is_relative_to(PROJECT_ROOT) else str(run_dir),
              "agent_model": next(iter(runs.values()))["agent_model"] if runs else None,
              "n_boot": n_boot, "seed": seed, "conditions": {}}
    for condition in conditions:
        condition_metrics = {}
        for name, (kind, field) in METRICS.items():
            keys = [key for key in runs if key[0] == condition and ((key[3] is None) == (kind == "benign"))]
            successes = sum(bool(runs[key][field]) for key in keys)
            metric = {"n": len(keys), "k": successes, "rate": round(successes / len(keys), 4) if keys else None,
                      "ci95": wilson(successes, len(keys))}
            if condition != "C0":
                pairs = [(key[1:], int(bool(runs[("C0",) + key[1:]][field])), int(bool(runs[key][field])))
                         for key in keys if ("C0",) + key[1:] in runs]
                c0_only = sum(1 for _, x_c0, x_condition in pairs if x_c0 and not x_condition)
                condition_only = sum(1 for _, x_c0, x_condition in pairs if x_condition and not x_c0)
                metric["paired_vs_C0"] = {
                    "n_pairs": len(pairs), "c0_only": c0_only, "cond_only": condition_only,
                    "diff": round((condition_only - c0_only) / len(pairs), 4) if pairs else None,
                    "p_mcnemar": float(f"{mcnemar_exact(c0_only, condition_only):.3g}"),
                    "diff_ci95_cluster_bootstrap": cluster_bootstrap_diff(pairs, n_boot, seed)}
            condition_metrics[name] = metric
        # Gate-attributable failures (blocked call + user task failed).
        gate_failures = {"benign": [], "attack": []}
        for key, run in runs.items():
            if key[0] != condition or run["utility"] or not any(e["blocked"] for e in run["trace"]):
                continue
            c0_run = runs.get(("C0",) + key[1:])
            gate_failures["benign" if key[3] is None else "attack"].append({
                "suite": key[1], "user_task": key[2], "injection_task": key[3],
                "blocked_by": blocked_policies(run), "blocks": blocked_decisions(run),
                "c0_utility": None if c0_run is None else bool(c0_run["utility"])})
        condition_metrics["gate_failures"] = {
            kind: {"n": len(cases), "c0_succeeded": sum(1 for x in cases if x["c0_utility"]),
                   "by_policy": dict(Counter(p for x in cases for p in x["blocked_by"])),
                   "cases": sorted(cases, key=lambda x: (x["suite"], x["user_task"], str(x["injection_task"])))}
            for kind, cases in gate_failures.items()}
        result["conditions"][condition] = condition_metrics
    # Agreement between conditions on benign runs without any block (agent nondeterminism).
    compared_pairs = flips = 0
    benign_tasks = {key[1:3] for key in runs if key[3] is None}
    for task in benign_tasks:
        task_runs = [runs[(c,) + task + (None,)] for c in conditions if (c,) + task + (None,) in runs]
        task_runs = [r for r in task_runs if not any(e["blocked"] for e in r["trace"])]
        for i in range(len(task_runs)):
            for j in range(i + 1, len(task_runs)):
                compared_pairs += 1
                flips += task_runs[i]["utility"] != task_runs[j]["utility"]
    result["benign_unblocked_condition_pairs"] = {"pairs": compared_pairs, "utility_differs": flips,
                                                  "rate": round(flips / compared_pairs, 4) if compared_pairs else None}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    summary = analyse(run_dir, args.n_boot, args.seed)
    (run_dir / "significance.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    for condition, condition_metrics in summary["conditions"].items():
        cells = []
        for name in METRICS:
            metric = condition_metrics[name]
            cell = f"{name} {metric['k']}/{metric['n']}"
            if "paired_vs_C0" in metric:
                paired = metric["paired_vs_C0"]
                cell += f" (Δ {paired['diff']:+.3f}, p={paired['p_mcnemar']:.2g})"
            cells.append(cell)
        gate_failures = condition_metrics["gate_failures"]
        print(condition, "; ".join(cells),
              f"; gate failures benign {gate_failures['benign']['n']} attack {gate_failures['attack']['n']}")
    print("benign unblocked flips:", summary["benign_unblocked_condition_pairs"])


if __name__ == "__main__":
    main()
