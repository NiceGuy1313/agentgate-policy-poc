"""P0 사용자 고정 설정: v2와 v3이 함께 쓰는 정책이다(config/v2/user_policy.yaml, config/v3/policy.yaml의 user_policy). v1에는 없다."""

from __future__ import annotations

from agentgate.gate.base import Policy, PolicyContext, ToolCall


class UserFixedPolicy(Policy):
    """P0: the user's fixed settings apply to every task, on top of the TaskSpec."""

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
        return self.require_human(call, context)

    def require_human(self, call: ToolCall, context: PolicyContext):
        """v3.1 overrides this (D2)."""
        return self.escalate(f"User policy requires human confirmation for '{call.tool}'.", layer="user_policy",
                             rule="always_human", force_human=True)
