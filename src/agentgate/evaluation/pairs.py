"""Stratified pair list for live evaluation: every user task x K injection tasks of its suite.

Within each suite the injection tasks are shuffled once (seed) and dealt round-robin, so each user task gets
K distinct injection tasks and each injection task appears floor/ceil(n_user * K / n_inj) times.
With benign=True every user task also gets one no-attack entry ('none').
"""

from __future__ import annotations

import random

from agentgate import SUITES
from agentgate.suites import load_suite, task_number


def stratified_pairs(k: int, seed: int, benign: bool, suites=SUITES) -> list[tuple[str, str, str]]:
    pairs = []
    for suite_name in suites:
        suite = load_suite(suite_name)
        user_task_ids = sorted(suite.user_tasks, key=task_number)
        injection_task_ids = sorted(suite.injection_tasks, key=task_number)
        if k > len(injection_task_ids):
            raise ValueError(f"{suite_name}: k={k} > {len(injection_task_ids)} injection tasks")
        order = injection_task_ids[:]
        random.Random(f"{seed}:{suite_name}").shuffle(order)
        for i, user_task_id in enumerate(user_task_ids):
            if benign:
                pairs.append((suite_name, user_task_id, "none"))
            for j in range(k):
                pairs.append((suite_name, user_task_id, order[(i * k + j) % len(order)]))
    return pairs
