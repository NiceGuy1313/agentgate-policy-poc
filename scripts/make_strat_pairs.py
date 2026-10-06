"""Stratified pair list for live evaluation: every user task x K injection tasks of its suite.

Within each suite the injection tasks are shuffled once (--seed) and dealt round-robin, so each user task gets
K distinct injection tasks and each injection task appears floor/ceil(n_user * K / n_inj) times.
With --benign every user task also gets one no-attack entry ('none').

  python scripts/make_strat_pairs.py --k 3 --benign --out data/pairs/strat3_seed0.txt
  python scripts/live_eval.py --agent llm --pairs @data/pairs/strat3_seed0.txt --conditions C0 C1 C2 C4 ...
"""

from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

from agentgate import SUITES
from agentgate.suites import load_suite


def _num(x: str) -> int:
    return int(x.split("_")[-1])


def stratified_pairs(k: int, seed: int, benign: bool, suites=SUITES) -> list[tuple[str, str, str]]:
    out = []
    for s in suites:
        suite = load_suite(s)
        uts = sorted(suite.user_tasks, key=_num)
        its = sorted(suite.injection_tasks, key=_num)
        if k > len(its):
            raise ValueError(f"{s}: k={k} > {len(its)} injection tasks")
        order = its[:]
        random.Random(f"{seed}:{s}").shuffle(order)
        for i, ut in enumerate(uts):
            if benign:
                out.append((s, ut, "none"))
            for j in range(k):
                out.append((s, ut, order[(i * k + j) % len(order)]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3, help="injection tasks per user task")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--benign", action="store_true", help="add one no-attack run per user task")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pairs = stratified_pairs(a.k, a.seed, a.benign)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("".join(f"{s}:{u}:{i}\n" for s, u, i in pairs))
    att = [p for p in pairs if p[2] != "none"]
    per_inj = Counter((s, i) for s, _, i in att)
    print(f"{len(pairs)} entries ({len(att)} attack, {len(pairs) - len(att)} benign) -> {a.out}")
    for s in SUITES:
        c = [v for (ss, _), v in per_inj.items() if ss == s]
        print(f"  {s}: {sum(c)} attack pairs, {len(c)} injection tasks, {min(c)}-{max(c)} each")


if __name__ == "__main__":
    main()
