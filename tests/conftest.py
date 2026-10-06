import pytest

from agentgate.config import policies_config
from agentgate.policy.base import Call, Context
from agentgate.policy.engine import PolicyEngine
from agentgate.session import SessionState
from agentgate.spec import TaskSpec
from agentgate.trace import ValueIndex


class Synth:
    """Synthetic trace builder for policy unit tests."""

    def __init__(self, prompt: str, spec: TaskSpec | None = None, **spec_kw):
        self.spec = spec or TaskSpec(**spec_kw)
        cfg = policies_config()
        self.index = ValueIndex(prompt, self.spec, cfg.get("self_identifiers"))
        self.session = SessionState()
        self.engine = PolicyEngine()
        self.step = 0

    def read(self, tool: str, args: dict | None = None, output="", raw=None):
        src = self.index.add_response(self.step, tool, args or {}, raw if raw is not None else output, str(output))
        self.session.record(tool, args or {}, raw, src.sensitive, src.sid)
        self.step += 1
        return self

    def did(self, tool: str, args: dict, raw=None):
        """Record an executed state-changing call (e.g. an e-mail the agent sent)."""
        return self.read(tool, args, "", raw)

    @property
    def ctx(self):
        return Context(self.spec, self.index, self.session, self.engine.config)

    def decide(self, tool: str, **args):
        return self.engine.decide(Call(tool, args), self.ctx)

    def by(self, tool: str, policy: str, **args):
        """Decision of one policy (None if it did not produce one)."""
        res = self.decide(tool, **args)
        return next((d for d in res.decisions if d.policy_id == policy), None)


@pytest.fixture
def synth():
    return Synth
