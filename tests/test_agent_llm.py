"""ClaudeAgentLLM with a fake Anthropic client: request shape and byte-exact replay of assistant turns."""

from anthropic.types import Message

from agentgate.agent_llm import RAW_KEY, ClaudeAgentLLM
from agentgate.llm.client import supports_temperature
from agentgate.pipeline import RecordingPipeline, build_pipeline
from agentgate.runtime import GatedRuntime
from agentgate.spec import TaskSpec
from agentgate.suites import load_suite


def _msg(content, stop):
    return Message.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                   "content": content, "stop_reason": stop, "stop_sequence": None,
                                   "usage": {"input_tokens": 10, "output_tokens": 5}})


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = self

    def stream(self, **kw):
        self.requests.append(kw)
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
    s = load_suite("banking")
    fc = FakeClient([_msg(TURN1, "tool_use"), _msg(TURN2, "end_turn")])
    llm = ClaudeAgentLLM(fc, "claude-opus-5-5", effort="medium")
    pipe = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    env = s.load_and_inject_default_environment({})
    rt = GatedRuntime(s.tools, mode="enforce")
    pipe.query("What is my balance?", rt, env)
    first, second = fc.requests
    assert "temperature" not in first and first["output_config"] == {"effort": "medium"}
    assert first["max_tokens"] == 16000 and first["model"] == "claude-opus-5-5"
    # the second request replays turn 1 exactly as returned (interleaved thinking/tool_use order kept)
    asst = [m for m in second["messages"] if m["role"] == "assistant"][0]
    assert asst["content"] == TURN1
    # both tool results come back in one user message, after the assistant turn
    results = second["messages"][-1]
    assert results["role"] == "user" and [b["tool_use_id"] for b in results["content"]] == ["t1", "t2"]
    assert [e.tool for e in rt.trace] == ["get_balance", "get_iban"]
    assert llm.usage["calls"] == 2 and llm.usage["input_tokens"] == 20
    assert pipe.messages[-1]["content"][0]["content"] == "Done."


def test_unanswered_tool_use_is_not_replayed():
    fc = FakeClient([_msg([{"type": "tool_use", "id": "t9", "name": "get_balance", "input": {}}], "max_tokens")])
    llm = ClaudeAgentLLM(fc, "claude-opus-5-5")
    s = load_suite("banking")
    from agentdojo.types import ChatUserMessage, text_content_block_from_string
    msgs = [ChatUserMessage(role="user", content=[text_content_block_from_string("hi")])]
    _, _, _, out, _ = llm.query("hi", GatedRuntime(s.tools), s.load_and_inject_default_environment({}), msgs)
    assert out[-1]["tool_calls"] is None and out[-1][RAW_KEY] == []
    assert llm.usage["max_tokens_stops"] == 1
