"""live 평가용 층화 쌍 목록을 만든다: 모든 user task x 같은 suite의 injection task K개.

suite마다 injection task를 seed로 한 번 섞은 뒤 round-robin으로 배분하므로, 각 user task는 서로 다른 injection
task K개를 받고 각 injection task는 floor/ceil(n_user * K / n_inj)회 나온다.
benign=True이면 user task마다 공격 없는 항목('none')을 하나 더 넣는다.
"""

from __future__ import annotations

import random

from agentgate import SUITES
from agentgate.agentdojo.suites import load_suite, task_number


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
