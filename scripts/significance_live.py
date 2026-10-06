"""Confidence intervals and paired tests for a live run, against C0.

For each condition and metric (ASR, Utility, Utility under attack):
  - rate with a Wilson 95% interval,
  - paired difference to C0 on the same (suite, user task, injection task) with the exact McNemar p-value,
  - 95% interval of the difference from a cluster bootstrap over user tasks (stratified by suite), because
    the runs of one user task are not independent.
Also counts gate-attributable failures: runs that had a blocked call and failed the user task
(and the subset that succeeded under C0), with the blocking policies.

  python scripts/significance_live.py --run results/live/v1/<name>
Writes <run>/significance.json. Runs with an error are dropped (pairwise for the paired tests).
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = {"asr": ("attack", "security"), "utility": ("benign", "utility"), "utility_under_attack": ("attack", "utility")}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p (binomial on the discordant pairs)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def load(run: Path) -> dict:
    R = {}
    for f in run.glob("C*/*/*/*.json"):
        r = json.loads(f.read_text())
        if r.get("error"):
            continue
        R[(r["condition"], r["suite"], r["user_task"], r["injection_task"])] = r
    return R


def blocked_policies(r: dict) -> list[str]:
    return sorted({d["policy_id"] for e in r["trace"] if e["blocked"] for d in e["decisions"] if d["verdict"] != "ALLOW"})


def cluster_bootstrap_diff(pairs: list[tuple[tuple, int, int]], n_boot: int, seed: int) -> tuple[float, float] | None:
    """pairs: (key, x_C0, x_C). Resample user tasks with replacement within each suite."""
    if not pairs:
        return None
    by_task = defaultdict(list)
    for k, x0, x1 in pairs:
        by_task[(k[0], k[1])].append(x1 - x0)
    by_suite = defaultdict(list)
    for t in by_task:
        by_suite[t[0]].append(t)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        tot = n = 0
        for tasks in by_suite.values():
            for _ in range(len(tasks)):
                v = by_task[tasks[rng.randrange(len(tasks))]]
                tot += sum(v)
                n += len(v)
        diffs.append(tot / n)
    diffs.sort()
    return round(diffs[int(0.025 * n_boot)], 4), round(diffs[int(0.975 * n_boot) - 1], 4)


def analyse(run: Path, n_boot: int = 2000, seed: int = 0) -> dict:
    R = load(run)
    conds = sorted({k[0] for k in R})
    out = {"run": str(run.relative_to(ROOT)) if run.is_relative_to(ROOT) else str(run),
           "agent_model": next(iter(R.values()))["agent_model"] if R else None,
           "n_boot": n_boot, "seed": seed, "conditions": {}}
    for c in conds:
        M = {}
        for name, (kind, field) in METRICS.items():
            keys = [k for k in R if k[0] == c and ((k[3] is None) == (kind == "benign"))]
            k_ = sum(bool(R[k][field]) for k in keys)
            m = {"n": len(keys), "k": k_, "rate": round(k_ / len(keys), 4) if keys else None, "ci95": wilson(k_, len(keys))}
            if c != "C0":
                pairs = [(k[1:], int(bool(R[("C0",) + k[1:]][field])), int(bool(R[k][field])))
                         for k in keys if ("C0",) + k[1:] in R]
                b = sum(1 for _, x0, x1 in pairs if x0 and not x1)     # C0 true, C false
                cc = sum(1 for _, x0, x1 in pairs if x1 and not x0)    # C true, C0 false
                m["paired_vs_C0"] = {
                    "n_pairs": len(pairs), "c0_only": b, "cond_only": cc,
                    "diff": round((cc - b) / len(pairs), 4) if pairs else None,
                    "p_mcnemar": float(f"{mcnemar_exact(b, cc):.3g}"),
                    "diff_ci95_cluster_bootstrap": cluster_bootstrap_diff(pairs, n_boot, seed)}
            M[name] = m
        # gate-attributable failures (blocked call + user task failed)
        gate_fail = {"benign": [], "attack": []}
        for k, r in R.items():
            if k[0] != c or r["utility"] or not any(e["blocked"] for e in r["trace"]):
                continue
            r0 = R.get(("C0",) + k[1:])
            gate_fail["benign" if k[3] is None else "attack"].append({
                "suite": k[1], "user_task": k[2], "injection_task": k[3], "blocked_by": blocked_policies(r),
                "c0_utility": None if r0 is None else bool(r0["utility"])})
        M["gate_failures"] = {
            kind: {"n": len(v), "c0_succeeded": sum(1 for x in v if x["c0_utility"]),
                   "by_policy": dict(Counter(p for x in v for p in x["blocked_by"])),
                   "cases": sorted(v, key=lambda x: (x["suite"], x["user_task"], str(x["injection_task"])))}
            for kind, v in gate_fail.items()}
        out["conditions"][c] = M
    # agreement between conditions on benign runs without any block (agent nondeterminism)
    same = flips = 0
    tasks = {k[1:3] for k in R if k[3] is None}
    for t in tasks:
        rs = [R[(c,) + t + (None,)] for c in conds if (c,) + t + (None,) in R]
        rs = [r for r in rs if not any(e["blocked"] for e in r["trace"])]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                same += 1
                flips += rs[i]["utility"] != rs[j]["utility"]
    out["benign_unblocked_condition_pairs"] = {"pairs": same, "utility_differs": flips,
                                               "rate": round(flips / same, 4) if same else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    run = Path(a.run).resolve()
    S = analyse(run, a.n_boot, a.seed)
    (run / "significance.json").write_text(json.dumps(S, indent=1, ensure_ascii=False))
    for c, M in S["conditions"].items():
        cells = []
        for name in METRICS:
            m = M[name]
            s = f"{name} {m['k']}/{m['n']}"
            if "paired_vs_C0" in m:
                pv = m["paired_vs_C0"]
                s += f" (Δ {pv['diff']:+.3f}, p={pv['p_mcnemar']:.2g})"
            cells.append(s)
        g = M["gate_failures"]
        print(c, "; ".join(cells), f"; gate failures benign {g['benign']['n']} attack {g['attack']['n']}")
    print("benign unblocked flips:", S["benign_unblocked_condition_pairs"])


if __name__ == "__main__":
    main()
