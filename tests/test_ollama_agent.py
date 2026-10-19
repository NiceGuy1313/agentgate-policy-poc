"""fake /api/chat(Ollama API docs의 응답 형식)으로 OllamaAgentLLM을 검사한다."""

import json

import pytest

from agentgate.agents.ollama_agent import OllamaAgentLLM, parse_tool_arguments
from agentgate.core.runtime import GatedRuntime
from agentgate.core.task_spec import TaskSpec
from agentgate.llm import client
from agentgate.llm.client import OllamaClient
from agentgate.llm.taskspec_extractor import TaskSpecExtractor
from agentgate.policy_gate.rule_checker import RuleChecker
from agentgate.policy_gate.runtime import PolicyGateRuntime
from agentgate.scenarios.pipeline import RecordingPipeline, build_pipeline
from agentgate.scenarios.suites import load_suite


class FakeResponse:
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

    monkeypatch.setattr(client.requests, "post", post)
    return sent, replies


TOOLS_TURN = {"model": "glm-4.6", "done": True, "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 30,
              "message": {"role": "assistant", "content": "", "thinking": "need balance",
                          "tool_calls": [{"function": {"name": "get_balance", "arguments": {}}},
                                         {"function": {"name": "get_iban", "arguments": "{}"}},
                                         {"function": {"name": "bad name!", "arguments": {}}}]}}
FINAL_TURN = {"model": "glm-4.6", "done": True, "done_reason": "stop", "prompt_eval_count": 200, "eval_count": 10,
              "message": {"role": "assistant", "content": "Your balance is 1810.0."}}


def test_parse_tool_arguments():
    assert parse_tool_arguments({"a": 1}) == {"a": 1}
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments("not json") == {"__raw__": "not json"}


def test_agent_round_trip(fake_post):
    sent, replies = fake_post
    replies += [FakeResponse(TOOLS_TURN), FakeResponse(FINAL_TURN)]
    suite = load_suite("banking")
    llm = OllamaAgentLLM("glm-4.6", client=OllamaClient(host="https://ollama.com", api_key="k"))
    pipeline = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    runtime = GatedRuntime(suite.tools, mode="enforce")
    pipeline.query("What is my balance?", runtime, suite.load_and_inject_default_environment({}))

    first, second = sent[0], sent[1]
    assert first["url"] == "https://ollama.com/api/chat"
    assert first["headers"]["Authorization"] == "Bearer k"
    assert first["json"]["stream"] is False and first["json"]["options"]["temperature"] == 0.0
    assert {t["function"]["name"] for t in first["json"]["tools"]} >= {"get_balance", "send_money"}
    assert first["json"]["messages"][0]["role"] == "system"
    # executed calls went through the gate; the invalid name was dropped
    assert [event.tool for event in runtime.trace] == ["get_balance", "get_iban"]
    assert llm.usage["invalid_tool_calls"] == 1
    # second request: assistant turn replayed with only the valid calls, then one tool message per call
    messages = second["json"]["messages"]
    assistant_message = next(m for m in messages if m["role"] == "assistant")
    assert [tc["function"]["name"] for tc in assistant_message["tool_calls"]] == ["get_balance", "get_iban"]
    assert assistant_message["thinking"] == "need balance"
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_name"] for m in tool_messages] == ["get_balance", "get_iban"]
    assert llm.usage["input_tokens"] == 320 and llm.usage["calls"] == 2
    assert pipeline.messages[-1]["content"][0]["content"] == "Your balance is 1810.0."


def test_blocked_call_result_reaches_model(fake_post):
    sent, replies = fake_post
    turn = {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "update_password", "arguments": {"password": "x"}}}]},
            "prompt_eval_count": 1, "eval_count": 1}
    replies += [FakeResponse(turn), FakeResponse(FINAL_TURN)]
    suite = load_suite("banking")
    llm = OllamaAgentLLM("glm-4.6", client=OllamaClient(host="http://localhost:11434", api_key=""))
    pipeline = RecordingPipeline(build_pipeline(llm, lambda p: (TaskSpec(), {"source": "test"})))
    runtime = PolicyGateRuntime(suite.tools, mode="enforce", judge=RuleChecker())
    pipeline.query("hi", runtime, suite.load_and_inject_default_environment({}))
    assert runtime.trace[0].blocked and "Authorization" not in sent[0]["headers"]
    tool_msg = next(m for m in sent[1]["json"]["messages"] if m["role"] == "tool")
    assert tool_msg["content"].startswith("Tool call blocked by policy gate (R1, R6): ")


def test_retry_then_error(fake_post, monkeypatch):
    sent, replies = fake_post
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    replies += [FakeResponse({"error": "busy"}, 503), FakeResponse({"error": "bad"}, 400)]
    ollama_client = OllamaClient(host="https://ollama.com", api_key="k")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        ollama_client.chat({"model": "m", "messages": []})
    assert len(sent) == 2


def test_cloud_requires_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(client, "load_dotenv", lambda: None)
    with pytest.raises(RuntimeError):
        OllamaClient(host="https://ollama.com", api_key=None)


def test_extractor_reuses_cache_without_client(tmp_path):
    extractor = TaskSpecExtractor(cache_dir=tmp_path, model="claude-sonnet-5-5")
    assert extractor._client is None and extractor.model == "claude-sonnet-5-5"


def test_retry_on_connection_error(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise client.requests.ConnectionError("proxy closed")
        return FakeResponse(FINAL_TURN)

    monkeypatch.setattr(client.requests, "post", post)
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    ollama_client = OllamaClient(host="https://ollama.com", api_key="k")
    assert ollama_client.chat({"model": "m", "messages": []})["message"]["content"].startswith("Your balance")
    assert len(calls) == 3
