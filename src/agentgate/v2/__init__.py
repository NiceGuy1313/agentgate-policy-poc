"""v2: 판별기 + Trusted LLM (live C5). v3에서 폐기한 구성이다.

정책: P0(사용자 고정 설정) + P1–P9(v1 클래스 그대로) + P10(판별기 라벨). 설정: config/v2/policies.yaml.
툴 응답마다 판별기(PIGuard, config/v2/detector.yaml)가 라벨을 붙이고(`agentgate.v2.runtime`), ESCALATE는
Trusted LLM v2(`agentgate.v2.judge`)가 처리한다. TaskSpec 추출 프롬프트는 v1과 같은 prompts/v1/extract_taskspec.md다.
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml
from agentgate.gate.user_policy import UserFixedPolicy
from agentgate.v1 import policies as v1_policies
from agentgate.v2.policies import DetectorLabelPolicy

VERSION = "v2"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v1" / "extract_taskspec.md"
POLICY_CLASSES = [UserFixedPolicy, *v1_policies.POLICY_CLASSES, DetectorLabelPolicy]


def load_config() -> dict:
    config = dict(load_config_yaml("v2/policies.yaml"))
    config["user_policy"] = load_config_yaml("v2/user_policy.yaml")
    return config


def make_engine(disabled: set[str] | None = None):
    from agentgate.gate.engine import PolicyEngine
    return PolicyEngine(load_config(), disabled, POLICY_CLASSES)


__all__ = ["POLICY_CLASSES", "TASKSPEC_PROMPT", "VERSION", "load_config", "make_engine"]
