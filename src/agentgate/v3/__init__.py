"""v3·v3.1: ToolSpec + 정책 문서 + 판정 LLM v3 (live C6, C7, C7n). 판별기는 쓰지 않는다.

정책: P0 + P1–P9 + P11(툴 콘텐츠가 시킨 행동) + P12(요청된 행동만), P7 공격 문구 없음.
설정·사용자 고정 설정·정책 문서: config/v3/policy.yaml 한 파일. 위협 통제 영역(controls)과 환경 바인딩(environment)을
`compile_engine_config`가 P0–P12 클래스가 읽는 엔진 설정으로 바꾸고, v3.1은 그 파일의 `v31` 절을 덮어쓴다.
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
GATES = {"v3": POLICY_CLASSES, "v31": POLICY_CLASSES_V31}
POLICY_FILE = "v3/policy.yaml"


def _tools_with(tools: dict, key: str) -> dict:
    return {name: spec[key] for name, spec in tools.items() if key in spec}


def compile_engine_config(doc: dict) -> dict:
    """Turn policy.yaml (threat controls + environment binding) into the engine config the P0–P12 classes read."""
    controls, tools = doc["controls"], doc["environment"]["tools"]
    egress, funds = controls["data_egress"], controls["funds"]
    return {
        "version": doc["version"],
        "enabled": [p for control in controls.values() for p in control["engine_policies"]],
        "self_identifiers": doc["environment"]["self_identifiers"],
        "P1": {"targets": _tools_with(tools, "destination")},
        "external_sinks": _tools_with(tools, "egress"),
        "P2": {"min_overlap_chars": egress["overlap_min_chars"],
               "high_value_token_patterns": egress["high_value_patterns"],
               "sensitive_categories": egress["sensitive_data"]},
        "P3": {"min_distinct_reads": egress["bulk_min_sources"],
               "count_attachments": egress["bulk_count_attachments"]},
        "P4": {"tools": _tools_with(tools, "amount"),
               "session_cumulative_limit": funds["session_cumulative_limit"],
               "repeat_new_recipient_threshold": funds["repeat_recipient_threshold"]},
        "P5": {"email_delete_tools": [t for t, kind in _tools_with(tools, "deletes").items() if kind == "message"],
               "file_delete_tools": [t for t, kind in _tools_with(tools, "deletes").items() if kind == "document"]},
        "P6": {"tools": list(_tools_with(tools, "privilege_change")),
               "password_args": _tools_with(tools, "credential")},
        "P7": {"message_tools": _tools_with(tools, "message"), "visit_tools": _tools_with(tools, "visit_url"),
               "instruction_phrases": controls["untrusted_content"]["instruction_phrases"]},
        "P8": {"tools": _tools_with(tools, "booking_target")},
        "P9": {"tools": list(_tools_with(tools, "schedule_change"))},
        "reversibility": {"irreversible": [t for t, spec in tools.items() if not spec["reversible"]],
                          "reversible": [t for t, spec in tools.items() if spec["reversible"]]},
        "runtime": dict(doc["runtime"]),
        "user_policy": doc["user_policy"],
    }


def load_config(gate: str = "v3") -> dict:
    doc = load_config_yaml(POLICY_FILE)
    config = compile_engine_config(doc)
    if gate == "v31":
        v31 = doc["v31"]
        config["version"] = v31["version"]
        config["runtime"] = {**config["runtime"], **v31["runtime"]}
        config["v31"] = v31["fixes"]
    return config


def make_engine(disabled: set[str] | None = None, gate: str = "v3"):
    from agentgate.gate.engine import PolicyEngine
    return PolicyEngine(load_config(gate), disabled, GATES[gate])


def load_rules(gate: str = "v3") -> list[dict]:
    doc = load_config_yaml(POLICY_FILE)
    overrides = doc["v31"]["rule_overrides"] if gate == "v31" else {}
    return [{**rule, **overrides.get(rule["id"], {})} for rule in doc["rules"]]


__all__ = ["GATES", "POLICY_CLASSES", "POLICY_CLASSES_V31", "TASKSPEC_PROMPT", "VERSION", "compile_engine_config",
           "load_config", "load_rules", "make_engine"]
