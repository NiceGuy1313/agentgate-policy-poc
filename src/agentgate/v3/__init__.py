"""v3·v3.1: ToolSpec + 정책 문서 + 판정 LLM v3 (live C6, C7, C7n). 판별기는 쓰지 않는다.

정책: P0 + P1–P9 + P11(툴 콘텐츠가 시킨 행동) + P12(요청된 행동만), P7 공격 문구 없음.
설정: config/v3/policies_v3.yaml(v3), policies_v31.yaml(v3.1). 정책 문서: config/v3/policy_rules_v3*.yaml.
툴 응답은 ToolSpec 추출기(`agentgate.v3.toolspec`)가 JSON으로 바꾸고(`agentgate.v3.runtime`), ESCALATE는 판정 LLM
v3(`agentgate.v3.judge`)가 처리한다. TaskSpec 추출 프롬프트: prompts/v3/extract_taskspec.md(delegated_sources 포함).
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml
from agentgate.gate.user_policy import UserFixedPolicy
from agentgate.v1 import policies as v1
from agentgate.v3.policies import (
    AccountPermissionPolicyV31,
    BulkEgressPolicyV31,
    CalendarChangePolicyV31,
    InstructedActionPolicy,
    InstructedActionPolicyV31,
    RequestedActionPolicy,
    ReservationPolicyV31,
    UserFixedPolicyV31,
)

VERSION = "v3"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v3" / "extract_taskspec.md"
POLICY_CLASSES = [UserFixedPolicy, *v1.POLICY_CLASSES, InstructedActionPolicy, RequestedActionPolicy]
POLICY_CLASSES_V31 = [UserFixedPolicyV31, v1.DestinationProvenancePolicy, v1.SensitiveEgressPolicy,
                      BulkEgressPolicyV31, v1.MoneyTransferPolicy, v1.DeletionPolicy, AccountPermissionPolicyV31,
                      v1.UntrustedContentPolicy, ReservationPolicyV31, CalendarChangePolicyV31,
                      InstructedActionPolicyV31, RequestedActionPolicy]
GATES = {"v3": ("v3/policies_v3.yaml", POLICY_CLASSES, "v3/policy_rules_v3.yaml"),
         "v31": ("v3/policies_v31.yaml", POLICY_CLASSES_V31, "v3/policy_rules_v31.yaml")}


def load_config(gate: str = "v3") -> dict:
    config = dict(load_config_yaml(GATES[gate][0]))
    config["user_policy"] = load_config_yaml("v3/user_policy.yaml")
    return config


def make_engine(disabled: set[str] | None = None, gate: str = "v3"):
    from agentgate.gate.engine import PolicyEngine
    return PolicyEngine(load_config(gate), disabled, GATES[gate][1])


def rules_path(gate: str = "v3"):
    return PROJECT_ROOT / "config" / GATES[gate][2]


__all__ = ["GATES", "POLICY_CLASSES", "POLICY_CLASSES_V31", "TASKSPEC_PROMPT", "VERSION", "load_config",
           "make_engine", "rules_path"]
