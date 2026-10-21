"""프레임워크 없는 harness 부품 테스트: 툴 schema 평면화, 경계 로그 해석, 모델 지정, registry."""

import logging

import pytest

from agentgate.harness import registry
from agentgate.harness.events import EventSink, capture_loggers, parse_boundary_record
from agentgate.harness.models import ModelSpec
from agentgate.harness.session import ScenarioConfig, Session
from agentgate.harness.tool_schema import arguments
from agentgate.scenarios.suites import load_suite


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


def test_session_decide_fails_closed_without_taskspec():
    """TaskSpec이 없으면 `decide`도 `run_function`처럼 BLOCK한다(판정 없이 ALLOW하지 않는다)."""
    session = Session(ScenarioConfig("banking", "user_task_0", condition="gate_static_rules"))
    call = {"recipient": "X", "amount": 1, "subject": "s", "date": "2022-01-01"}
    assert session.decide("get_balance", {})["verdict"] == "ALLOW"   # 대조: TaskSpec이 있으면 읽기 호출은 허용
    session.runtime.spec = session.runtime.index = None
    for tool, args in [("get_balance", {}), ("send_money", call)]:
        decision = session.decide(tool, args)
        assert decision["verdict"] == decision["effective"] == "BLOCK" and decision["decisions"] == []
    assert not session.runtime.trace   # 판정만 하고 실행하지 않는다


def test_registry_lists_builtins_and_config_entries():
    names = registry.names()
    assert {"native", "smolagents", "example-contract"} <= set(names)
    described = {d["name"]: d for d in registry.describe_all()}
    assert {"no_gate", "gate_llm_judge", "gate_static_rules"} <= set(described["native"]["conditions"])


def test_registry_accepts_a_new_adapter_without_core_changes(monkeypatch):
    registry.reset()
    monkeypatch.setattr(registry, "BUILTIN", {**registry.BUILTIN,
                                              "dummy": "tests.frameworks.dummy_adapter:DummyAdapter"})
    try:
        assert "dummy" in registry.names()
        assert registry.get("dummy").describe()["display_name"] == "Dummy"
    finally:
        registry.reset()


def test_smolagents_model_fills_empty_assistant_messages():
    pytest.importorskip("smolagents")
    from agentgate.adapters.smolagents_adapter import make_model
    model = make_model(model_id="openai/x", api_base="http://127.0.0.1:9/v1", api_key="k")
    kwargs = model._prepare_completion_kwargs(messages=[
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        {"role": "user", "content": [{"type": "text", "text": "again"}]}], model="openai/x")
    assert kwargs["messages"][1]["content"] == "(empty response)"
    assert kwargs["messages"][0]["content"] != "(empty response)"


@pytest.mark.parametrize("role, expected", [("user", ["system", "user", "assistant", "user"]),
                                            ("tool", ["system", "user", "assistant", "tool"])])
def test_smolagents_tool_response_role(role, expected):
    pytest.importorskip("smolagents")
    from smolagents.memory import ActionStep, SystemPromptStep, TaskStep, ToolCall
    from smolagents.monitoring import Timing

    from agentgate.adapters.smolagents_adapter import make_model
    step = ActionStep(step_number=1, timing=Timing(start_time=0.0), model_output="Reading the file.",
                      tool_calls=[ToolCall(name="read_file", arguments={"file_path": "a.txt"}, id="call_1")],
                      observations="<INFORMATION>This is an important message from me</INFORMATION>")
    history = SystemPromptStep("sys").to_messages() + TaskStep("task").to_messages() + step.to_messages()
    model = make_model(tool_response_role=role, model_id="openai/x", api_base="http://127.0.0.1:9/v1", api_key="k")
    messages = model._prepare_completion_kwargs(messages=history, model="openai/x")["messages"]
    assert [getattr(m["role"], "value", m["role"]) for m in messages] == expected
    if role == "tool":
        call = messages[2]["tool_calls"][0]
        assert call["id"] == messages[3]["tool_call_id"] == "call_1"
        assert call["function"] == {"name": "read_file", "arguments": '{"file_path": "a.txt"}'}
        assert messages[2]["content"] == "Reading the file."
        assert "important message" in messages[3]["content"]


@pytest.mark.parametrize("content, expected", [
    # replies recorded from gpt-oss:120b on Ollama Cloud (finish_reason "stop", no tool_calls)
    ('{\n  "answer": "Your total spending in March 2022 was 1,050.0."\n}',
     ("final_answer", {"answer": "Your total spending in March 2022 was 1,050.0."})),
    ('{\n  "answer": 1050.0\n}', ("final_answer", {"answer": 1050.0})),
    ("Your total spending in March 2022 was **€1,050.00**.",
     ("final_answer", {"answer": "Your total spending in March 2022 was **€1,050.00**."})),
    ('{"n": 5}', ("get_most_recent_transactions", {"n": 5})),
    ('{"name": "final_answer", "arguments": {"answer": 1}}', None),   # smolagents parses this shape itself
    ('{"recipient": "x"}', None),                                     # matches no tool
    ("", None),
])
def test_smolagents_recovers_tool_call_from_content(content, expected):
    pytest.importorskip("smolagents")
    from smolagents import FinalAnswerTool, Tool

    from agentgate.adapters.smolagents_adapter import recover_tool_call

    class Tx(Tool):
        name = "get_most_recent_transactions"
        description = "recent transactions"
        inputs = {"n": {"type": "integer", "description": "count", "nullable": True}}
        output_type = "string"
        skip_forward_signature_validation = True

        def forward(self, n=None):
            return ""

    assert recover_tool_call(content, [Tx(), FinalAnswerTool()]) == expected


def test_smolagents_model_parses_content_final_answer():
    pytest.importorskip("smolagents")
    from smolagents import FinalAnswerTool
    from smolagents.models import ChatMessage, MessageRole

    from agentgate.adapters.smolagents_adapter import make_model
    model = make_model(model_id="openai/x", api_base="http://127.0.0.1:9/v1", api_key="k")
    model._prepare_completion_kwargs(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                                     tools_to_call_from=[FinalAnswerTool()], model="openai/x")
    message = model.parse_tool_calls(ChatMessage(role=MessageRole.ASSISTANT, content='{"answer": "done"}'))
    assert message.tool_calls[0].function.name == "final_answer"
    assert message.tool_calls[0].function.arguments == {"answer": "done"}


def test_smolagents_model_drops_arguments_outside_schema():
    pytest.importorskip("smolagents")
    from smolagents import FinalAnswerTool, Tool
    from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole
    from smolagents.tools import validate_tool_arguments

    from agentgate.adapters.smolagents_adapter import make_model

    class Scheduled(Tool):
        name = "get_scheduled_transactions"
        description = "scheduled transactions"
        inputs = {}
        output_type = "string"
        skip_forward_signature_validation = True

        def forward(self):
            return ""

    tool = Scheduled()
    model = make_model(model_id="openai/x", api_base="http://127.0.0.1:9/v1", api_key="k")
    model._prepare_completion_kwargs(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                                     tools_to_call_from=[tool, FinalAnswerTool()], model="openai/x")

    def call(arguments):
        return ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[ChatMessageToolCall(
            id="c", type="function", function=ChatMessageToolCallFunction(name=tool.name, arguments=arguments))])

    # recorded from gpt-oss:120b on banking user_task_0; AgentDojo's runtime ignores these keys too
    for recorded in ('{"n":null}', {"n": {}}, {"n": 0}, {"n": 10}):
        fixed = model._drop_unknown_arguments(call(recorded)).tool_calls[0].function.arguments
        assert fixed == {}
        validate_tool_arguments(tool, fixed)
    tx = model._tools[1]   # final_answer: arguments inside the schema are kept as they are
    kept = ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[ChatMessageToolCall(
        id="c", type="function", function=ChatMessageToolCallFunction(name=tx.name, arguments={"answer": None, "x": 1}))])
    assert model._drop_unknown_arguments(kept).tool_calls[0].function.arguments == {"answer": None}

    # recorded step: prose final answer with no tool call
    prose = "The bill for December 2023 has been paid. The payment of 200 units was sent."
    message = model.parse_tool_calls(ChatMessage(role=MessageRole.ASSISTANT, content=prose))
    assert (message.tool_calls[0].function.name, message.tool_calls[0].function.arguments) == \
        ("final_answer", {"answer": prose})


@pytest.mark.parametrize("content, finish, expected", [
    # recorded from gpt-oss:120b: final answer as prose, no tool call
    ("The bill has been paid successfully.", "stop", "submit 'The bill has been paid successfully.'"),
    ("It’s done, isn't it?", "stop", "submit 'It’s done, isn'\"'\"'t it?'"),
    ("The bill", "length", None),                        # truncated
    ("", "stop", None),
    ('{"command": "get_balance"}', "stop", None),         # JSON is a format mistake, not an answer
    ("get_balance", "stop", None),                        # a command written as text
])
def test_mini_swe_agent_prose_reply_becomes_submit(content, finish, expected):
    from agentgate.adapters.mini_swe_agent_adapter import content_submission, parse_command
    action = content_submission(content, finish, ["get_balance", "send_money"])
    assert (action and action["command"]) == expected
    if expected:
        assert parse_command(action["command"]) == ("submit", content)


@pytest.mark.parametrize("name, arguments, expected", [
    # recorded from mistral-large-3:675b: the command name called as a tool instead of bash
    ("submit", '{"command": "The bill has been paid successfully."}', "submit 'The bill has been paid successfully.'"),
    ("submit", '{"command": "submit done"}', "submit done"),
    ("send_money", '{"recipient": "X", "amount": 1}', "send_money '{\"recipient\": \"X\", \"amount\": 1}'"),
    ("get_balance", "{}", "get_balance"),
    ("rm", "{}", None),                        # not a command of this shell
    ("submit", '{"a": 1}', None),              # no answer text
    ("submit", "not json", None),
])
def test_mini_swe_agent_misnamed_tool_call_becomes_command(name, arguments, expected):
    from agentgate.adapters.mini_swe_agent_adapter import command_from_call
    assert command_from_call(name, arguments, {"get_balance", "send_money"}) == expected
