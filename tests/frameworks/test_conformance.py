"""Every registered framework must gate every tool call like the native pipeline does (LLM-free)."""

import pytest

from agentgate.harness import registry
from agentgate.harness.conformance import run_case

PAIRS = [("banking", "user_task_0", "injection_task_0"), ("banking", "user_task_2", None)]


def _available():
    names = []
    for name in registry.names():
        adapter = registry.get(name)
        if adapter.unavailable_reason() is None and not registry.entry(name).get("python"):
            names.append(name)
    return names


@pytest.mark.parametrize("framework", _available())
@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: ":".join(x or "none" for x in p))
@pytest.mark.parametrize("condition", ["C0", "C1"])
def test_framework_conformance(framework, pair, condition):
    result = run_case(framework, pair, condition)
    assert result["ok"], result["problems"]


def test_third_party_adapter_conforms(monkeypatch):
    registry.reset()
    monkeypatch.setattr(registry, "BUILTIN", {**registry.BUILTIN,
                                              "dummy": "tests.frameworks.dummy_adapter:DummyAdapter"})
    try:
        result = run_case("dummy", PAIRS[0], "C1")
        assert result["ok"], result["problems"]
    finally:
        registry.reset()
