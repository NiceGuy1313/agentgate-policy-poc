"""v1: 결정론 정책 P1–P9 (config/v1/policies.yaml). ESCALATE는 block / allow / 판정 LLM v1(C4)로 처리한다.

TaskSpec 추출 프롬프트: prompts/v1/extract_taskspec.md. 실험 조건: `agentgate.v1.conditions`.
"""

from __future__ import annotations

from agentgate.config import PROJECT_ROOT, load_config_yaml
from agentgate.v1.policies import POLICY_CLASSES

VERSION = "v1"
TASKSPEC_PROMPT = PROJECT_ROOT / "prompts" / "v1" / "extract_taskspec.md"


def load_config() -> dict:
    return load_config_yaml("v1/policies.yaml")


def make_engine(disabled: set[str] | None = None):
    from agentgate.gate.engine import PolicyEngine
    return PolicyEngine(load_config(), disabled, POLICY_CLASSES)


__all__ = ["POLICY_CLASSES", "TASKSPEC_PROMPT", "VERSION", "load_config", "make_engine"]
