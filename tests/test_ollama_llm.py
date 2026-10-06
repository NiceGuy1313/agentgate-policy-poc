"""OllamaAgentLLM against a fake /api/chat (response shapes from the Ollama API docs)."""

import json

import pytest

from agentgate import ollama_llm as O
from agentgate.pipeline import RecordingPipeline, build_pipeline
from agentgate.runtime import GatedRuntime
from agentgate.spec import TaskSpec
from agentgate.suites import load_suite


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data


@pytest.fixture
def fake_post(monkeypatch):
    sent, replies = [], []

    def post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json})
        return replies.pop(0)

    monkeypatch.setattr(O.requests, "post", post)
    return sent, replies


TOOLS_TURN = {"model": "glm-4.6", "done": True, "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 30,
              "message": {"role": "assistant", "content": "", "thinking": "need balance",
                          "tool_calls": [{"function": {"name": "get_balance", "arguments": {}}},
                                         {"function": {"name": "get_iban", "arguments": "{}"}},
                                         {"function": {"name": "bad name!", "arguments": {}}}]}}
FINAL_TURN = {"model": "glm-4.6", "done": True, "done_reason": "stop", "prompt_eval_count": 200, "eval_count": 10,
              "message": {"role": "assistant", "content": "Your balance is 1810.0."}}


def test_parse_arguments():
    assert O.parse_arguments({"a": 1}) == {"a": 1}
    assert O.parse_arguments('{"a": 1}') == {"a": 1}
    assert O.parse_arguments("not json") == {"__raw__": "not json"}


def test_agent_round_trip(fake_post):
    sent, replies = fake_post
    replies += [FakeResp(TOOLS_TURN), FakeResp(FINAL_TURN)]
    s = load_suite("banking")
    llm = O.OllamaAgentLLM("glm-4.6", client=O.OllamaClient(host="https://ollama.com", api_key="k"))
    pipe = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    rt = GatedRuntime(s.tools, mode="enforce")
    pipe.query("What is my balance?", rt, s.load_and_inject_default_environment({}))

    first, second = sent[0], sent[1]
    assert first["url"] == "https://ollama.com/api/chat"
    assert first["headers"]["Authorization"] == "Bearer k"
    assert first["json"]["stream"] is False and first["json"]["options"]["temperature"] == 0.0
    assert {t["function"]["name"] for t in first["json"]["tools"]} >= {"get_balance", "send_money"}
    assert first["json"]["messages"][0]["role"] == "system"
    # executed calls went through the gate; the invalid name was dropped
    assert [e.tool for e in rt.trace] == ["get_balance", "get_iban"]
    assert llm.usage["invalid_tool_calls"] == 1
    # second request: assistant turn replayed with only the valid calls, then one tool message per call
    msgs = second["json"]["messages"]
    asst = next(m for m in msgs if m["role"] == "assistant")
    assert [tc["function"]["name"] for tc in asst["tool_calls"]] == ["get_balance", "get_iban"]
    assert asst["thinking"] == "need balance"
    tools = [m for m in msgs if m["role"] == "tool"]
    assert [m["tool_name"] for m in tools] == ["get_balance", "get_iban"]
    assert llm.usage["input_tokens"] == 320 and llm.usage["calls"] == 2
    assert pipe.messages[-1]["content"][0]["content"] == "Your balance is 1810.0."


def test_blocked_call_result_reaches_model(fake_post):
    sent, replies = fake_post
    turn = {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "update_password", "arguments": {"password": "x"}}}]},
            "prompt_eval_count": 1, "eval_count": 1}
    replies += [FakeResp(turn), FakeResp(FINAL_TURN)]
    s = load_suite("banking")
    llm = O.OllamaAgentLLM("glm-4.6", client=O.OllamaClient(host="http://localhost:11434", api_key=""))
    pipe = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    rt = GatedRuntime(s.tools, mode="enforce")
    pipe.query("hi", rt, s.load_and_inject_default_environment({}))
    assert rt.trace[0].blocked and "Authorization" not in sent[0]["headers"]
    tool_msg = next(m for m in sent[1]["json"]["messages"] if m["role"] == "tool")
    assert "blocked by security policy P6" in tool_msg["content"]


def test_retry_then_error(fake_post, monkeypatch):
    sent, replies = fake_post
    monkeypatch.setattr(O.time, "sleep", lambda s: None)
    replies += [FakeResp({"error": "busy"}, 503), FakeResp({"error": "bad"}, 400)]
    c = O.OllamaClient(host="https://ollama.com", api_key="k")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        c.chat({"model": "m", "messages": []})
    assert len(sent) == 2


def test_cloud_requires_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(O, "load_dotenv", lambda: None)
    with pytest.raises(RuntimeError):
        O.OllamaClient(host="https://ollama.com", api_key=None)


def test_extractor_reuses_cache_without_client(tmp_path):
    from agentgate.llm.extractor import TaskSpecExtractor
    ex = TaskSpecExtractor(cache_dir=tmp_path, model="claude-sonnet-5-5")
    assert ex._client is None and ex.model == "claude-sonnet-5-5"


def test_retry_on_connection_error(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise O.requests.ConnectionError("proxy closed")
        return FakeResp(FINAL_TURN)

    monkeypatch.setattr(O.requests, "post", post)
    monkeypatch.setattr(O.time, "sleep", lambda s: None)
    c = O.OllamaClient(host="https://ollama.com", api_key="k")
    assert c.chat({"model": "m", "messages": []})["message"]["content"].startswith("Your balance")
    assert len(calls) == 3
