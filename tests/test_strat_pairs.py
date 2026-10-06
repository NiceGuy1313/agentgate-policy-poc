from collections import Counter

from scripts.make_strat_pairs import stratified_pairs


def test_every_user_task_gets_k_distinct_injections_and_one_benign():
    pairs = stratified_pairs(3, 0, benign=True)
    per_ut = Counter((s, u) for s, u, i in pairs if i != "none")
    assert len(per_ut) == 97 and set(per_ut.values()) == {3}
    assert sum(1 for p in pairs if p[2] == "none") == 97
    for (s, u) in per_ut:
        its = [i for ss, uu, i in pairs if (ss, uu) == (s, u) and i != "none"]
        assert len(set(its)) == 3


def test_injections_balanced_within_suite_and_seed_reproducible():
    pairs = stratified_pairs(3, 0, benign=False)
    per_inj = Counter((s, i) for s, _, i in pairs)
    for s in {s for s, _ in per_inj}:
        c = [v for (ss, _), v in per_inj.items() if ss == s]
        assert max(c) - min(c) <= 1
    assert len(per_inj) == 35 and len(pairs) == 291
    assert pairs == stratified_pairs(3, 0, benign=False)
