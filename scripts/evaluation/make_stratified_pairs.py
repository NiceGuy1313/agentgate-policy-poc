"""Stratified pair list for live evaluation: every user task x K injection tasks of its suite.

The sampling itself is `agentgate.evaluation.pairs.stratified_pairs` (--seed, --benign are passed through).

Usage:
  python scripts/evaluation/make_stratified_pairs.py --k 3 --benign --out data/pairs/strat3_seed0.txt
  python scripts/evaluation/live_evaluation.py --agent llm --pairs @data/pairs/strat3_seed0.txt \\
      --conditions C0 C1 C2 C4 ...
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from agentgate import SUITES
from agentgate.evaluation.pairs import stratified_pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3, help="injection tasks per user task")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--benign", action="store_true", help="add one no-attack run per user task")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    pairs = stratified_pairs(args.k, args.seed, args.benign)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(f"{suite}:{user_task}:{injection_task}\n"
                                      for suite, user_task, injection_task in pairs))
    attack_pairs = [p for p in pairs if p[2] != "none"]
    pairs_per_injection = Counter((suite, injection_task) for suite, _, injection_task in attack_pairs)
    print(f"{len(pairs)} entries ({len(attack_pairs)} attack, {len(pairs) - len(attack_pairs)} benign) -> {args.out}")
    for suite_name in SUITES:
        counts = [count for (suite, _), count in pairs_per_injection.items() if suite == suite_name]
        print(f"  {suite_name}: {sum(counts)} attack pairs, {len(counts)} injection tasks, "
              f"{min(counts)}-{max(counts)} each")


if __name__ == "__main__":
    main()
