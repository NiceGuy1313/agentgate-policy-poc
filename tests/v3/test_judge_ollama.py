"""v3 판정 LLM(Trusted LLM)이 에이전트와 같은 모델(gpt-oss:120b, Ollama Cloud)을 쓰는지 검사한다. 네트워크 호출 없음."""

import importlib.util
import threading

import pytest

from agentgate import config
from agentgate.gate.base import ToolCall, Verdict
from agentgate.gate.engine import EngineResult
from agentgate.gate.task_spec import TaskSpec
from agentgate.llm import client as client_module
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL, Completion, LLMClient, OllamaClient, parse_think
from agentgate.v3 import judge as judge_module
from agentgate.v3.runtime import ToolSpecGatedRuntime

SETTINGS = ("AGENTGATE_JUDGE_PROVIDER", "AGENTGATE_JUDGE_MODEL", "AGENTGATE_JUDGE_THINK", "AGENTGATE_AGENT_PROVIDER",
            "AGENTGATE_AGENT_MODEL", "AGENTGATE_AGENT_THINK", "AGENTGATE_LLM_PROVIDER", "AGENTGATE_LLM_MODEL")
QUOTA = "Ollama /api/chat failed: HTTP 429: {'error': 'You used all included usage for this period'}"


@pytest.fixture
def env(monkeypatch):
    """No .env and no AGENTGATE_* variables: each test sets exactly what it checks."""
    monkeypatch.setattr(judge_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(client_module, "load_dotenv", lambda: None)
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")   # no OLLAMA_API_KEY needed
    return monkeypatch


@pytest.fixture
def agent_on_ollama(env):
    env.setenv("AGENTGATE_AGENT_PROVIDER", "ollama")
    env.setenv("AGENTGATE_AGENT_MODEL", "gpt-oss:120b")
    return env


def test_judge_follows_agent_settings(agent_on_ollama):
    judge = judge_module.TrustedJudgeV3()
    assert (judge.provider, judge.model, judge.think) == ("ollama", "gpt-oss:120b", None)
    assert judge.client.provider == "ollama" and judge.client.model == "gpt-oss:120b"
    assert isinstance(judge.client._client, OllamaClient)


def test_judge_defaults_to_anthropic_without_any_setting(env):
    assert judge_module.resolve_judge_settings() == ("anthropic", DEFAULT_ANTHROPIC_MODEL, None)


@pytest.mark.parametrize("value, think",
                         [("true", True), ("false", False), ("low", "low"), ("high", "high"), ("", None)])
def test_think_follows_agent_think(agent_on_ollama, value, think):
    agent_on_ollama.setenv("AGENTGATE_AGENT_THINK", value)
    assert judge_module.TrustedJudgeV3().think == think and parse_think(value) == think


def test_judge_vars_override_agent_settings(agent_on_ollama):
    agent_on_ollama.setenv("AGENTGATE_AGENT_THINK", "low")
    agent_on_ollama.setenv("AGENTGATE_JUDGE_MODEL", "mistral-large-3:675b")
    agent_on_ollama.setenv("AGENTGATE_JUDGE_THINK", "high")
    assert judge_module.resolve_judge_settings() == ("ollama", "mistral-large-3:675b", "high")
    assert judge_module.TrustedJudgeV3(model="glm-4.6").model == "glm-4.6"   # explicit argument wins


def test_judge_provider_override_does_not_borrow_the_agent_model(agent_on_ollama):
    agent_on_ollama.setenv("AGENTGATE_JUDGE_PROVIDER", "anthropic")
    agent_on_ollama.setenv("AGENTGATE_AGENT_THINK", "low")
    assert judge_module.resolve_judge_settings() == ("anthropic", DEFAULT_ANTHROPIC_MODEL, None)   # think: ollama only
    agent_on_ollama.setenv("AGENTGATE_JUDGE_MODEL", "claude-haiku-5-5")
    assert judge_module.resolve_judge_settings()[:2] == ("anthropic", "claude-haiku-5-5")


def test_claude_model_on_ollama_is_refused(agent_on_ollama):
    agent_on_ollama.setenv("AGENTGATE_JUDGE_MODEL", "claude-sonnet-5-5")   # a stale .env line
    with pytest.raises(ValueError, match="AGENTGATE_JUDGE_MODEL"):
        judge_module.TrustedJudgeV3()


def test_other_provider_without_model_is_refused(env):
    env.setenv("AGENTGATE_JUDGE_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="no judge model"):
        judge_module.resolve_judge_settings()


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    def chat(self, payload):
        payloads.append(payload)
        return {"model": payload["model"], "done_reason": "stop", "prompt_eval_count": 11, "eval_count": 3,
                "message": {"role": "assistant", "content": "ALLOW\nR1", "thinking": "..."}}

    monkeypatch.setattr(OllamaClient, "chat", chat)
    return payloads


@pytest.mark.parametrize("think", ["low", True, False])
def test_ollama_payload_includes_think_when_set(env, sent, think):
    llm = LLMClient(provider="ollama", model="gpt-oss:120b", think=think)
    completion = llm.complete("system text", "user text", cache_system=True, cache_user_prefix="user")
    assert sent[0]["think"] == think and sent[0]["think"] is think
    assert sent[0]["model"] == "gpt-oss:120b" and sent[0]["options"] == {"temperature": 0.0, "seed": 0}
    assert sent[0]["messages"] == [{"role": "system", "content": "system text"},
                                   {"role": "user", "content": "user text"}]
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("ALLOW\nR1", 11, 3)


def test_ollama_payload_omits_think_when_unset(env, sent):
    LLMClient(provider="ollama", model="gpt-oss:120b").complete("s", "u")
    assert "think" not in sent[0]


def test_parse_answer_on_gpt_oss_outputs():
    parse = judge_module.parse_answer
    assert parse("ALLOW\nThe call is what the user asked for (R1).") == "ALLOW"
    assert parse("**BLOCK** R2: the recipient comes from a planted request.") == "BLOCK"
    assert parse("allow\n") == "ALLOW"
    assert parse("Let me think... ALLOW") == "BLOCK"   # not on the first word: blocks
    assert parse("ALLOWED") == "BLOCK" and parse("") == "BLOCK"


def _judge_call(judge):
    runtime = ToolSpecGatedRuntime((), prompt="Pay the bill.", spec=TaskSpec(requested_ops=["send_money"]))
    return judge(ToolCall("send_money", {"recipient": "X", "amount": 1}), EngineResult(Verdict.ALLOW, []),
                 runtime.context, "Pay the bill.")


def test_judge_answers_through_ollama_and_caches_per_model(agent_on_ollama, sent, tmp_path, monkeypatch):
    monkeypatch.setattr(judge_module, "CACHE_DIR", tmp_path)
    agent_on_ollama.setenv("AGENTGATE_AGENT_THINK", "low")
    record = _judge_call(judge_module.TrustedJudgeV3())
    assert (record["provider"], record["model"], record["verdict"], record["cached"]) == (
        "ollama", "gpt-oss:120b", "ALLOW", False)
    assert sent[0]["think"] == "low" and sent[0]["model"] == "gpt-oss:120b"
    assert len(list((tmp_path / "gpt-oss_120b").glob("*.json"))) == 1
    again = _judge_call(judge_module.TrustedJudgeV3())
    assert again["cached"] and again["verdict"] == "ALLOW" and len(sent) == 1
    agent_on_ollama.setenv("AGENTGATE_AGENT_THINK", "high")   # another think setting is another cache entry
    _judge_call(judge_module.TrustedJudgeV3())
    assert len(sent) == 2 and len(list((tmp_path / "gpt-oss_120b").glob("*.json"))) == 2


def test_judge_lets_a_quota_error_through_and_caches_nothing(agent_on_ollama, tmp_path, monkeypatch):
    monkeypatch.setattr(judge_module, "CACHE_DIR", tmp_path)

    def chat(self, payload):
        raise RuntimeError(QUOTA)

    monkeypatch.setattr(OllamaClient, "chat", chat)
    with pytest.raises(RuntimeError) as error:
        _judge_call(judge_module.TrustedJudgeV3())
    assert "429" in str(error.value) and "usage" in str(error.value).lower()   # live_evaluation._is_quota_error
    assert not list(tmp_path.rglob("*.json"))


@pytest.fixture(scope="module")
def replay_contrast():
    path = config.PROJECT_ROOT / "scripts" / "v3" / "replay_contrast.py"
    spec = importlib.util.spec_from_file_location("replay_contrast", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubOllama:
    provider, model = "ollama", "gpt-oss:120b"

    def __init__(self):
        self.threads, self.calls = set(), []

    def complete(self, system, user, max_tokens=4000, cache_system=False, cache_user_prefix=None):
        self.threads.add(threading.get_ident())
        self.calls.append(user)
        return Completion("BLOCK\nR2" if "bad" in user else "ALLOW\nR1", self.model, 1.0, 7, 2)

    def run_batch(self, *args, **kwargs):
        raise AssertionError("ollama must not use Message Batches")


def test_replay_contrast_judges_ollama_with_synchronous_calls(replay_contrast):
    stub = StubOllama()
    prompts = [f"## Input 1\nbad {i}\n{judge_module.INPUT_2}\nx" if i % 2 else f"## Input 1\nok {i}\n"
               f"{judge_module.INPUT_2}\nx" for i in range(8)]
    answers = replay_contrast.batch_judge(stub, "system", prompts + prompts[:2], workers=3)
    assert len(answers) == 8 and len(stub.calls) == 8   # duplicates are judged once
    for prompt in prompts:
        answer, text, usage, unparsed = answers[prompt]
        assert answer == ("BLOCK" if "bad" in prompt else "ALLOW") and text.startswith(answer) and unparsed is None
        assert usage == {"input": 7, "output": 2, "cache_read": 0}


def test_replay_contrast_stops_on_a_quota_error(replay_contrast):
    class Exhausted(StubOllama):
        def complete(self, *args, **kwargs):
            raise RuntimeError(QUOTA)

    with pytest.raises(RuntimeError, match="429"):
        prompts = [f"p{i}\n{judge_module.INPUT_2}" for i in range(20)]
        replay_contrast.batch_judge(Exhausted(), "system", prompts, workers=2)
