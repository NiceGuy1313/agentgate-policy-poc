"""v2 정책 P10: 모든 툴 호출을 Trusted LLM 판정(ALLOW/BLOCK)으로 넘긴다. P0은 `agentgate.gate.user_policy`.

v2는 규칙 정책(P1–P9)을 쓰지 않는다. 판정 근거는 TaskSpec, 사용자 허용 범위, 툴 응답에 대한 IPI detector 판단이다.
"""

from __future__ import annotations

from agentgate.gate.base import Policy, PolicyContext, ToolCall
from agentgate.gate.normalization import iterate_leaf_values, long_value_candidates
from agentgate.gate.provenance import Origin
from agentgate.v2.labels import value_detection


class TrustedReviewPolicy(Policy):
    """P10: every tool call goes to the Trusted LLM (ESCALATE), reads included.

    Reads only the detector labels the runtime attached to the responses; never runs a model. The evidence lists,
    for argument values taken from tool responses, the detector label of that response.
    """

    policy_id = "P10"
    LONG_VALUE_CHARS = 200    # longer values (message bodies) are traced through the typed values inside them
    MIN_VALUE_CHARS = 3

    def applies_to(self, tool):
        return True

    def evaluate(self, call: ToolCall, context: PolicyContext):
        labels = []
        for arg, raw in call.args.items():
            for value in iterate_leaf_values(raw):
                for candidate in long_value_candidates(value, self.LONG_VALUE_CHARS):
                    if len(candidate.strip()) < self.MIN_VALUE_CHARS:
                        continue
                    provenance = context.value_index.origin_of(candidate)
                    if provenance.origin not in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL):
                        continue
                    label = value_detection(context.value_index, candidate, provenance.source_id)
                    if label is not None:
                        labels.append({"arg": arg, "value": candidate, **provenance.as_dict(), "detection": label})
        flagged = [x for x in labels if x["detection"]["flagged"] or x["detection"]["in_suspicious_span"]]
        reason = (f"{len(flagged)} argument value(s) of '{call.tool}' come from a response the detector flagged."
                  if flagged else f"'{call.tool}' needs a decision.")
        return self.escalate(reason, detector_flagged=bool(flagged), value_sources=labels[:10])
