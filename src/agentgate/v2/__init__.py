"""v2: IPI detector + Trusted LLM. 규칙 정책(P1–P9)은 쓰지 않는다.

정책: P0(사용자 허용 범위: delete_file, update_password는 사람 확인) + P10(그 밖의 모든 툴 호출을 Trusted LLM이
ALLOW/BLOCK 판정). 설정: config/v2/user_policy.yaml(허용 범위), config/v2/detector.yaml(판별기). 툴 응답마다 IPI detector(config/v2/detector.yaml)가 라벨을 붙이고
(`agentgate.v2.runtime`), Trusted LLM v2(`agentgate.v2.judge`)가 TaskSpec + 사용자 허용 범위 + 판별 결과로 판정한다.
TaskSpec 추출 프롬프트는 prompts/v1/extract_taskspec.md다.
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml
from agentgate.gate.user_policy import UserFixedPolicy
from agentgate.v2.policies import TrustedReviewPolicy

VERSION = "v2"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v1" / "extract_taskspec.md"
POLICY_CLASSES = [UserFixedPolicy, TrustedReviewPolicy]


# Settings the Trusted LLM input needs (v2 has no rule policies; config/v2/ holds only user_policy.yaml, detector.yaml)
SELF_IDENTIFIERS = ["emma.johnson@bluesparrowtech.com"]   # shown as SELF origin
IRREVERSIBLE_TOOLS = ["send_email", "send_direct_message", "send_channel_message", "post_webpage", "send_money",
                      "update_password", "delete_file", "remove_user_from_slack", "share_file", "get_webpage"]


def load_config() -> dict:
    return {"version": VERSION, "enabled": [p.policy_id for p in POLICY_CLASSES],
            "self_identifiers": list(SELF_IDENTIFIERS), "reversibility": {"irreversible": list(IRREVERSIBLE_TOOLS)},
            "runtime": {"escalate_action": "judge", "min_string_field_len": 4},
            "user_policy": load_config_yaml("v2/user_policy.yaml")}


def make_engine(disabled: set[str] | None = None):
    from agentgate.gate.engine import PolicyEngine
    return PolicyEngine(load_config(), disabled, POLICY_CLASSES)


__all__ = ["POLICY_CLASSES", "TASKSPEC_PROMPT", "VERSION", "load_config", "make_engine"]
