"""정책 단위 테스트용 synthetic trace builder(`synth` fixture)를 제공한다."""

import pytest

from agentgate.gate.base import PolicyContext, SessionState, ToolCall
from agentgate.gate.provenance import ValueIndex
from agentgate.gate.task_spec import TaskSpec


class SyntheticTrace:
    """Synthetic trace builder for policy unit tests."""

    def __init__(self, prompt: str, spec: TaskSpec | None = None, gate: str = "v1", **spec_kwargs):
        """gate: v1 | v2 | v3 | v31 - the version's engine (and, for v3, the ToolSpec value index)."""
        from agentgate import v1, v2, v3
        self.spec = spec or TaskSpec(**spec_kwargs)
        self.engine = {"v1": v1.make_engine, "v2": v2.make_engine}.get(gate, lambda: v3.make_engine(gate=gate))()
        index_class = ValueIndex
        if gate in ("v3", "v31"):
            from agentgate.v3.runtime import ToolSpecValueIndex as index_class
        runtime = self.engine.config["runtime"]
        self.value_index = index_class(prompt, self.spec, self.engine.config.get("self_identifiers"),
                                       runtime.get("min_string_field_len", 4), runtime.get("named_match_min_len", 4))
        self.session = SessionState()
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
