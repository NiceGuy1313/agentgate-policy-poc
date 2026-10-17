"""층화 쌍 표본 추출의 배분 균형과 seed 재현성을 검사한다."""

from collections import Counter

from agentgate.agentdojo.pairs import stratified_pairs


def test_every_user_task_gets_k_distinct_injections_and_one_benign():
    pairs = stratified_pairs(3, 0, benign=True)
    per_user_task = Counter((suite, user_task) for suite, user_task, injection in pairs if injection != "none")
    assert len(per_user_task) == 97 and set(per_user_task.values()) == {3}
    assert sum(1 for p in pairs if p[2] == "none") == 97
    for suite, user_task in per_user_task:
        injections = [i for s, u, i in pairs if (s, u) == (suite, user_task) and i != "none"]
        assert len(set(injections)) == 3


def test_injections_balanced_within_suite_and_seed_reproducible():
    pairs = stratified_pairs(3, 0, benign=False)
    per_injection = Counter((suite, injection) for suite, _, injection in pairs)
    for suite in {s for s, _ in per_injection}:
        counts = [v for (s, _), v in per_injection.items() if s == suite]
        assert max(counts) - min(counts) <= 1
    assert len(per_injection) == 35 and len(pairs) == 291
    assert pairs == stratified_pairs(3, 0, benign=False)
