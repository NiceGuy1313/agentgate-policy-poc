import pytest

from agentgate.config import load_policy_config
from agentgate.policy.base import PolicyContext, SessionState, ToolCall
from agentgate.policy.engine import PolicyEngine
from agentgate.provenance import ValueIndex
from agentgate.task_spec import TaskSpec


class SyntheticTrace:
    """Synthetic trace builder for policy unit tests."""

    def __init__(self, prompt: str, spec: TaskSpec | None = None, **spec_kwargs):
        self.spec = spec or TaskSpec(**spec_kwargs)
        policy_config = load_policy_config()
        self.value_index = ValueIndex(prompt, self.spec, policy_config.get("self_identifiers"))
        self.session = SessionState()
        self.engine = PolicyEngine()
        self.step = 0

    def read(self, tool: str, args: dict | None = None, output="", raw=None):
        source = self.value_index.add_response(self.step, tool, args or {}, raw if raw is not None else output,
                                               str(output))
        self.session.record_call(tool, args or {}, raw, source.sensitive_categories, source.source_id)
        self.step += 1
        return self

    def did(self, tool: str, args: dict, raw=None):
        """Record an executed state-changing call (e.g. an e-mail the agent sent)."""
        return self.read(tool, args, "", raw)

    @property
    def context(self):
        return PolicyContext(self.spec, self.value_index, self.session, self.engine.config)

    def decide(self, tool: str, **args):
        return self.engine.decide(ToolCall(tool, args), self.context)

    def by(self, tool: str, policy: str, **args):
        """Decision of one policy (None if it did not produce one)."""
        result = self.decide(tool, **args)
        return next((d for d in result.decisions if d.policy_id == policy), None)


@pytest.fixture
def synth():
    return SyntheticTrace
