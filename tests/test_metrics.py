from agentgate.evaluation.metrics import mcnemar_exact, wilson


def test_wilson_interval():
    assert wilson(5, 10) == (0.2366, 0.7634)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    assert wilson(0, 0) is None


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(0, 5) == 2 / 32
    assert mcnemar_exact(1, 9) == 2 * (1 + 10) / 1024
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(9, 1) == mcnemar_exact(1, 9)
