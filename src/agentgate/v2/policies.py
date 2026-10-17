"""v2 정책 P10: 판별기 라벨. P0은 `agentgate.gate.user_policy`, P1–P9는 v1 클래스를 그대로 쓴다."""

from __future__ import annotations

from agentgate.config import load_effect_tools
from agentgate.gate.base import Policy, PolicyContext, ToolCall
from agentgate.gate.normalization import iterate_leaf_values, long_value_candidates
from agentgate.gate.provenance import Origin
from agentgate.v1.policies import strictest_decision
from agentgate.v2.labels import value_detection


class DetectorLabelPolicy(Policy):
    """P10: an argument value taken from a response the detector flagged needs review.

    Reads only the DetectionResult labels the runtime attached to the responses; never runs a model.
    A value from a NAMED_SOURCE / OTHER_TOOL response that is flagged, or that sits inside a suspicious window,
    gives ESCALATE. P10 never blocks by itself: the Trusted LLM decides with the detector evidence.
    """

    policy_id = "P10"
    LONG_VALUE_CHARS = 200    # longer values (message bodies) are checked through the typed values inside them
    MIN_VALUE_CHARS = 3

    def __init__(self, config: dict):
        super().__init__(config)
        self.effect_tools = load_effect_tools()

    def applies_to(self, tool):
        return tool in self.effect_tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        decisions = []
        for arg, raw in call.args.items():
            for value in iterate_leaf_values(raw):
                for candidate in long_value_candidates(value, self.LONG_VALUE_CHARS):
                    if len(candidate.strip()) < self.MIN_VALUE_CHARS:
                        continue
                    provenance = context.value_index.origin_of(candidate)
                    if provenance.origin not in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL):
                        continue
                    label = value_detection(context.value_index, candidate, provenance.source_id)
                    if label is None or not (label["flagged"] or label["in_suspicious_span"]):
                        continue
                    evidence = {"arg": arg, "value": candidate, **provenance.as_dict(), "detection": label}
                    decisions.append(self.escalate(
                        f"'{candidate}' comes from a response the detector flagged "
                        f"(score {label['response_score']}, value window {label['value_score']}).", **evidence))
        return strictest_decision(decisions)
