"""fake Anthropic client로 ClaudeAgentLLM의 request 형식과 assistant 턴의 byte 단위 동일 재전송을 검사한다."""

from agentdojo.types import ChatUserMessage, text_content_block_from_string
from anthropic.types import Message

from agentgate.agents.claude_agent import RAW_CONTENT_KEY, ClaudeAgentLLM
from agentgate.llm.client import supports_temperature
from agentgate.pipeline import RecordingPipeline, build_pipeline
from agentgate.runtime import GatedRuntime
from agentgate.suites import load_suite
from agentgate.task_spec import TaskSpec


def _message(content, stop_reason):
    return Message.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                   "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                                   "usage": {"input_tokens": 10, "output_tokens": 5}})


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = self

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream(self.replies.pop(0))


TURN1 = [{"type": "thinking", "thinking": "", "signature": "sig1"},
         {"type": "tool_use", "id": "t1", "name": "get_balance", "input": {}},
         {"type": "thinking", "thinking": "", "signature": "sig2"},
         {"type": "tool_use", "id": "t2", "name": "get_iban", "input": {}}]
TURN2 = [{"type": "thinking", "thinking": "", "signature": "sig3"}, {"type": "text", "text": "Done."}]


def test_supports_temperature():
    assert not supports_temperature("claude-opus-5-5")
    assert not supports_temperature("claude-sonnet-5-5")
    assert supports_temperature("claude-haiku-4-5")


def test_request_shape_and_raw_replay():
    suite = load_suite("banking")
    client = FakeClient([_message(TURN1, "tool_use"), _message(TURN2, "end_turn")])
    llm = ClaudeAgentLLM(client, "claude-opus-5-5", effort="medium")
    pipeline = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    environment = suite.load_and_inject_default_environment({})
    runtime = GatedRuntime(suite.tools, mode="enforce")
    pipeline.query("What is my balance?", runtime, environment)
    first, second = client.requests
    assert "temperature" not in first and first["output_config"] == {"effort": "medium"}
    assert first["max_tokens"] == 16000 and first["model"] == "claude-opus-5-5"
    # the second request replays turn 1 exactly as returned (interleaved thinking/tool_use order kept)
    assistant_message = [m for m in second["messages"] if m["role"] == "assistant"][0]
    assert assistant_message["content"] == TURN1
    # both tool results come back in one user message, after the assistant turn
    results = second["messages"][-1]
    assert results["role"] == "user" and [b["tool_use_id"] for b in results["content"]] == ["t1", "t2"]
    assert [event.tool for event in runtime.trace] == ["get_balance", "get_iban"]
    assert llm.usage["calls"] == 2 and llm.usage["input_tokens"] == 20
    assert pipeline.messages[-1]["content"][0]["content"] == "Done."


def test_unanswered_tool_use_is_not_replayed():
    client = FakeClient([_message([{"type": "tool_use", "id": "t9", "name": "get_balance", "input": {}}],
                                  "max_tokens")])
    llm = ClaudeAgentLLM(client, "claude-opus-5-5")
    suite = load_suite("banking")
    messages = [ChatUserMessage(role="user", content=[text_content_block_from_string("hi")])]
    _, _, _, output, _ = llm.query("hi", GatedRuntime(suite.tools), suite.load_and_inject_default_environment({}),
                                   messages)
    assert output[-1]["tool_calls"] is None and output[-1][RAW_CONTENT_KEY] == []
    assert llm.usage["max_tokens_stops"] == 1
