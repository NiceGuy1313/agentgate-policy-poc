"""발췌: src/agentgate/policy/rules.py — 이 버전의 정의만 원문 그대로 옮긴 읽기용 사본이다.
import와 공유 helper는 원본에 있다. 생성: scripts/reporting/sync_version_folders.py
"""
# src/agentgate/policy/rules.py :: UserFixedPolicy
class UserFixedPolicy(Policy):
    """P0: the user's fixed settings (config/user_policy.yaml) apply to every task, on top of the TaskSpec."""

    policy_id = "P0"

    def __init__(self, config: dict):
        super().__init__(config)
        user_policy = config.get("user_policy") or {}
        self.always_block = set(user_policy.get("always_block") or [])
        self.always_human = set(user_policy.get("always_human") or [])

    def applies_to(self, tool):
        return tool in self.always_block or tool in self.always_human

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in self.always_block:
            return self.block(f"User policy blocks '{call.tool}' in every task.", layer="user_policy",
                              rule="always_block")
        if _v31(context, "human_unless_requested") and call.tool in context.spec.requested_ops:
            return None   # D2: the prompt itself asked for this action
        return self.escalate(f"User policy requires human confirmation for '{call.tool}'.", layer="user_policy",
                             rule="always_human", force_human=True)


# src/agentgate/policy/rules.py :: DetectorLabelPolicy
class DetectorLabelPolicy(Policy):
    """P10: an argument value taken from a response the detector flagged needs review (명세서.md 2절).

    Reads only the DetectionResult labels the runtime attached to the responses; never runs a model.
    A value from a NAMED_SOURCE / OTHER_TOOL response that is flagged, or that sits inside a suspicious chunk,
    gives ESCALATE; an OTHER_TOOL value inside a suspicious chunk gives BLOCK.
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
                for candidate in _detector_candidates(value, self.LONG_VALUE_CHARS):
                    if len(candidate.strip()) < self.MIN_VALUE_CHARS:
                        continue
                    provenance = context.value_index.origin_of(candidate)
                    if provenance.origin not in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL):
                        continue
                    label = context.value_index.value_detection(candidate, provenance.source_id)
                    if label is None or not (label["flagged"] or label["in_suspicious_span"]):
                        continue
                    evidence = {"arg": arg, "value": candidate, **provenance.as_dict(), "detection": label}
                    if provenance.origin == Origin.OTHER_TOOL and label["in_suspicious_span"]:
                        decisions.append(self.block(
                            f"'{candidate}' sits in a chunk the detector flagged, in an unrequested response.",
                            **evidence))
                    else:
                        decisions.append(self.escalate(
                            f"'{candidate}' comes from a response the detector flagged "
                            f"(score {label['response_score']}, value chunk {label['value_score']}).", **evidence))
        return _strictest_decision(decisions)


# src/agentgate/policy/rules.py :: _detector_candidates
def _detector_candidates(value, long_chars: int) -> list[str]:
    text = str(value)
    if len(text) <= long_chars:
        return [text]
    candidates = extract_typed_candidates(text)
    return sorted(candidates["url"] | candidates["email"] | candidates["iban"])
