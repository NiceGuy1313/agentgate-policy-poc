"""v3 정책 P11·P12와 v3.1 수정(A1·B1·C1·D2). P0은 `agentgate.gate.user_policy`, P1–P9는 v1 클래스다.

v3.1 수정은 v1 클래스를 고치지 않고 서브클래스로 둔다. 정책 문서: config/v3/policy_rules_v3.yaml, policy_rules_v31.yaml
"""

from __future__ import annotations

from agentgate.config import load_config_yaml, load_effect_tools
from agentgate.gate.base import Policy, PolicyContext, PolicyDecision, ToolCall
from agentgate.gate.normalization import iterate_leaf_values
from agentgate.gate.provenance import TRUSTED_ORIGINS, Origin
from agentgate.gate.user_policy import UserFixedPolicy
from agentgate.v1.policies import (
    AccountPermissionPolicy,
    BulkEgressPolicy,
    CalendarChangePolicy,
    ReservationPolicy,
)
from agentgate.v3.labels import instructed_requests


class InstructedActionPolicy(Policy):
    """P11 (rule R3): a call that carries out a request found inside a tool output.

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
        matches = instructed_requests(context.value_index, call.tool, call.args)
        if not matches:
            return None
        early = self.before_block(call, context, matches)
        if early is not None:
            return early
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

    def before_block(self, call: ToolCall, context: PolicyContext, matches: list[dict]) -> PolicyDecision | None:
        """v3.1 overrides this (B1)."""
        return None


class RequestedActionPolicy(Policy):
    """P12 (rule R1): state-changing / outbound tools must be requested by the user.

    Generalizes P6/P8/P9 to every effect tool except URL visits (P7 handles those). Unrequested calls that also
    carry out a tool-output request are blocked by P11; the rest go to the judge.
    """

    policy_id = "P12"
    EXCLUDED = ("visit",)

    def __init__(self, config: dict):
        super().__init__(config)
        roles = load_config_yaml("agentdojo/tool_roles.yaml")["effect"]
        self.tools = {t for cls, tools in roles.items() if cls not in self.EXCLUDED for t in tools}

    def applies_to(self, tool):
        return tool in self.tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in context.spec.requested_ops:
            return None
        return self.escalate(f"'{call.tool}' was not requested by the user.", tool=call.tool)


# ------------------------------------------------------------------------------------------- v3.1 (A1 B1 C1 D2)
class _DelegationEscalates:
    """A1: an unrequested action that matches a request in a delegated source goes to the judge, not BLOCK."""

    def unrequested(self, call: ToolCall, context: PolicyContext, label: str) -> PolicyDecision:
        if any(m["delegated"] for m in instructed_requests(context.value_index, call.tool, call.args)):
            return self.escalate(f"{label} '{call.tool}' was not requested directly but matches a request in a "
                                 "source the user delegated.", tool=call.tool, delegated=True)
        return super().unrequested(call, context, label)


class AccountPermissionPolicyV31(_DelegationEscalates, AccountPermissionPolicy):
    pass


class ReservationPolicyV31(_DelegationEscalates, ReservationPolicy):
    pass


class CalendarChangePolicyV31(_DelegationEscalates, CalendarChangePolicy):
    pass


class BulkEgressPolicyV31(BulkEgressPolicy):
    """C1: a bulk send the user asked for, to destinations from trusted or named sources, goes to the judge."""

    def bulk_decision(self, call: ToolCall, context: PolicyContext, evidence: dict) -> PolicyDecision:
        if _requested_egress_to_known_destination(self.config, call, context):
            return self.escalate(
                f"One outgoing call carries {evidence['count']} distinct read results, but the user asked to send "
                "data to this destination.", **evidence)
        return super().bulk_decision(call, context, evidence)


def _requested_egress_to_known_destination(config: dict, call: ToolCall, context: PolicyContext) -> bool:
    if not context.spec.egress_requested:
        return False
    destinations = [v for arg in (config.get("P1") or {}).get("targets", {}).get(call.tool, [])
                    for v in iterate_leaf_values(call.args.get(arg)) if str(v).strip()]
    if not destinations:
        return False
    allowed = TRUSTED_ORIGINS | {Origin.NAMED_SOURCE}
    return all(context.value_index.origin_of(v).origin in allowed for v in destinations)


class InstructedActionPolicyV31(InstructedActionPolicy):
    def before_block(self, call: ToolCall, context: PolicyContext, matches: list[dict]) -> PolicyDecision | None:
        visit_arg = (self.config.get("P7") or {}).get("visit_tools", {}).get(call.tool)
        if visit_arg:
            origin = context.value_index.origin_of(call.args.get(visit_arg)).origin
            if origin in TRUSTED_ORIGINS or origin == Origin.NAMED_SOURCE:
                # B1: opening a link inside a named source is reading, not an action the tool output imposed;
                # it may still be a planted request, so the judge decides (J3, J5) instead of a hard BLOCK
                return self.escalate(f"'{call.tool}' opens a link that a tool output asks to open, inside a source "
                                     "the user named.", matched_tool_output_requests=matches[:5], requested=False)
        return None


class UserFixedPolicyV31(UserFixedPolicy):
    def require_human(self, call: ToolCall, context: PolicyContext):
        if call.tool in context.spec.requested_ops:
            return None   # D2: the prompt itself asked for this action
        return super().require_human(call, context)
