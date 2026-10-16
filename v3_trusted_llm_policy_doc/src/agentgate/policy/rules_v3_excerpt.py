"""발췌: src/agentgate/policy/rules.py — 이 버전의 정의만 원문 그대로 옮긴 읽기용 사본이다.
import와 공유 helper는 원본에 있다. 생성: scripts/reporting/sync_version_folders.py
"""
# src/agentgate/policy/rules.py :: _v31
def _v31(context: PolicyContext, flag: str) -> bool:
    """v3.1 behaviour switches (config "v31"); absent in v1/v2/v3 configs, so those keep their decisions."""
    return bool((context.config.get("v31") or {}).get(flag))


# src/agentgate/policy/rules.py :: _delegated_request_match
def _delegated_request_match(call: ToolCall, context: PolicyContext) -> bool:
    """A1: the call carries out a request found in a source the user delegated (TaskSpec.delegated_sources)."""
    return any(m["delegated"] for m in context.value_index.instructed_requests(call.tool, call.args))


# src/agentgate/policy/rules.py :: _unrequested
def _unrequested(policy: Policy, call: ToolCall, context: PolicyContext, label: str) -> PolicyDecision:
    if _v31(context, "delegation_escalates") and _delegated_request_match(call, context):
        return policy.escalate(f"{label} '{call.tool}' was not requested directly but matches a request in a "
                               "source the user delegated.", tool=call.tool, delegated=True)
    return policy.block(f"{label} '{call.tool}' was not requested by the user.", tool=call.tool)


# src/agentgate/policy/rules.py :: _requested_egress_to_known_destination
def _requested_egress_to_known_destination(config: dict, call: ToolCall, context: PolicyContext) -> bool:
    """C1: the user asked to send data, and every destination comes from the prompt / user / directory or a
    source the user named."""
    if not context.spec.egress_requested:
        return False
    destinations = [v for arg in (config.get("P1") or {}).get("targets", {}).get(call.tool, [])
                    for v in iterate_leaf_values(call.args.get(arg)) if str(v).strip()]
    if not destinations:
        return False
    allowed = TRUSTED_ORIGINS | {Origin.NAMED_SOURCE}
    return all(context.value_index.origin_of(v).origin in allowed for v in destinations)


# src/agentgate/policy/rules.py :: InstructedActionPolicy
class InstructedActionPolicy(Policy):
    """P11 (rules R3, v3): a call that carries out a request found inside a tool output.

    Reads only the validated ToolSpec labels the runtime attached to the responses (no model calls here).
    Not requested by the user and not from a delegated source -> BLOCK; otherwise -> ESCALATE (judge rule J1).
    """

    policy_id = "P11"

    def __init__(self, config: dict):
        super().__init__(config)
        self.effect_tools = load_effect_tools()

    def applies_to(self, tool):
        return tool in self.effect_tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        matches = context.value_index.instructed_requests(call.tool, call.args)
        if not matches:
            return None
        visit_arg = (self.config.get("P7") or {}).get("visit_tools", {}).get(call.tool)
        if visit_arg and _v31(context, "visit_named_defers_to_p7"):
            origin = context.value_index.origin_of(call.args.get(visit_arg)).origin
            if origin in TRUSTED_ORIGINS or origin == Origin.NAMED_SOURCE:
                # B1: opening a link inside a named source is reading, not an action the tool output imposed;
                # it may still be a planted request, so the judge decides (J3, J5) instead of a hard BLOCK
                return self.escalate(f"'{call.tool}' opens a link that a tool output asks to open, inside a source "
                                     "the user named.", matched_tool_output_requests=matches[:5], requested=False)
        requested = call.tool in context.spec.requested_ops
        delegated = [m for m in matches if m["delegated"]]
        evidence = {"matched_tool_output_requests": matches[:5], "requested": requested}
        if not requested and not delegated:
            return self.block(f"'{call.tool}' carries out a request found in a tool output "
                              f"({matches[0]['source_id']}:{matches[0]['tool']}), which the user did not ask for.",
                              **evidence)
        return self.escalate(f"'{call.tool}' matches a request found in a tool output "
                             f"({'delegated source' if delegated else 'action also requested by the user'}).",
                             **evidence)


# src/agentgate/policy/rules.py :: RequestedActionPolicy
class RequestedActionPolicy(Policy):
    """P12 (rule R1, v3): state-changing / outbound tools must be requested by the user.

    Generalizes P6/P8/P9 to every effect tool except URL visits (P7 handles those). Unrequested calls that also
    carry out a tool-output request are blocked by P11; the rest go to the judge.
    """

    policy_id = "P12"
    EXCLUDED = ("visit",)

    def __init__(self, config: dict):
        super().__init__(config)
        from agentgate.config import load_config_yaml
        roles = load_config_yaml("tool_roles.yaml")["effect"]
        self.tools = {t for cls, tools in roles.items() if cls not in self.EXCLUDED for t in tools}

    def applies_to(self, tool):
        return tool in self.tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in context.spec.requested_ops:
            return None
        return self.escalate(f"'{call.tool}' was not requested by the user.", tool=call.tool)


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
