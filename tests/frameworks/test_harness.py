"""Harness pieces without a framework: tool schema flattening, boundary-log parsing, model specs, registry."""

import logging

import pytest

from agentgate.harness import registry
from agentgate.harness.events import EventSink, capture_loggers, parse_boundary_record
from agentgate.harness.models import ModelSpec
from agentgate.harness.tool_schema import arguments
from agentgate.suites import load_suite


def test_every_suite_tool_flattens():
    for suite_name in ("banking", "slack", "travel", "workspace"):
        for function in load_suite(suite_name).tools:
            schema = function.parameters.model_json_schema()
            args = arguments(schema)
            assert [a.name for a in args] == list(schema.get("properties", {}))
            for arg in args:
                assert arg.types and "null" not in arg.types
                assert arg.required == (arg.name in schema.get("required", []))


def test_optional_number_is_number_only():
    function = next(f for f in load_suite("banking").tools if f.name == "update_scheduled_transaction")
    amount = next(a for a in arguments(function.parameters.model_json_schema()) if a.name == "amount")
    assert amount.types == ["number"] and amount.nullable and not amount.required


def _record(msg, *args):
    return logging.LogRecord("smolagents", logging.DEBUG, __file__, 1, msg, args, None)


def test_boundary_record_parsing_reads_field_names_from_format():
    kind, step, message, data = parse_boundary_record(
        _record("[LLM_RESPONSE] step=%s purpose=%s actions=%s input_tokens=%s output_tokens=%s",
                3, "action", '[{"send_money": {"amount": 1}}]', 10, 2))
    assert (kind, step, message) == ("llm_response", 3, "send_money")
    assert data["input_tokens"] == 10 and data["actions"] == [{"send_money": {"amount": 1}}]
    kind, step, message, data = parse_boundary_record(
        _record("[TOOL_EXECUTION_END] action=%s returncode=%s duration_seconds=%.3f", "bash", 0, 0.12345))
    assert (kind, step, message, data["duration_seconds"]) == ("tool_end", None, "bash", 0.123)
    assert parse_boundary_record(_record("plain message")) is None


def test_concurrent_captures_route_to_own_sink():
    import threading
    sinks = [EventSink(), EventSink()]
    barrier = threading.Barrier(2)

    def work(sink, name):
        with capture_loggers(sink, ["smolagents"]):
            barrier.wait()
            logging.getLogger("smolagents").debug("[TOOL_EXECUTION_START] action=%s params=%s", name, "{}")
            barrier.wait()

    threads = [threading.Thread(target=work, args=(sink, f"tool{i}")) for i, sink in enumerate(sinks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [[e.data["action"] for e in s.events] for s in sinks] == [["tool0"], ["tool1"]]
    assert not logging.getLogger("smolagents").handlers


def test_model_spec_conversions(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.com")
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    spec = ModelSpec("ollama", "gpt-oss:120b")
    assert spec.litellm_kwargs()["model_id"] == "openai/gpt-oss:120b"
    assert spec.litellm_kwargs()["api_base"] == "https://ollama.com/v1"
    assert spec.openai_kwargs() == {"model": "gpt-oss:120b", "base_url": "https://ollama.com/v1", "api_key": "k"}
    monkeypatch.delenv("OLLAMA_API_KEY")
    monkeypatch.setattr("agentgate.harness.models.load_dotenv", lambda: None)
    assert spec.missing_key() == "OLLAMA_API_KEY"
    assert ModelSpec("ollama", "m", base_url="http://localhost:11434").missing_key() is None
    with pytest.raises(ValueError):
        ModelSpec("nope", "m")


def test_registry_lists_builtins_and_config_entries():
    names = registry.names()
    assert {"native", "smolagents", "example-contract"} <= set(names)
    described = {d["name"]: d for d in registry.describe_all()}
    assert "C3" in described["native"]["conditions"] and "C3" not in described["example-contract"]["conditions"]


def test_registry_accepts_a_new_adapter_without_core_changes(monkeypatch):
    registry.reset()
    monkeypatch.setattr(registry, "BUILTIN", {**registry.BUILTIN,
                                              "dummy": "tests.frameworks.dummy_adapter:DummyAdapter"})
    try:
        assert "dummy" in registry.names()
        assert registry.get("dummy").describe()["display_name"] == "Dummy"
    finally:
        registry.reset()
