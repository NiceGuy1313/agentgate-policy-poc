"""실험 조건 등록부: 조건 이름 → (Gate 모드, ESCALATE 처리, 판정기). 런타임 조립은 `agentgate.policy_gate.assembly`가 한다.

| 조건 | 내용 |
|---|---|
| no_gate | 방어 없음. Gate는 audit(호출을 기록만 하고 막지 않는다) |
| gate_llm_judge | 정책 Gate + 판정 LLM: effect 호출마다 판정 LLM(에이전트와 같은 모델)이 룰북(config/rulebook.yaml)과 TaskSpec + ToolSpec + 대기 중 호출로 ALLOW / BLOCK |
| gate_static_rules | 정책 Gate + 룰북 정적 코드: 같은 입력을 `agentgate.policy_gate.rule_checker`가 ALLOW / BLOCK. LLM 판정 호출 없음 |

조건을 비교할 때는 같은 실행(같은 짝·공격·모델·시점)에 no_gate를 함께 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    name: str
    mode: str             # audit | enforce
    escalate: str         # block | allow | judge
    description: str
    judge: str | None = None   # llm (판정 LLM) | rules (룰북 정적 코드), escalate == "judge"일 때

    @property
    def gated(self) -> bool:
        """Enforcing conditions use the requested TaskSpec source; the audit baseline only records."""
        return self.mode == "enforce"


CONDITIONS: dict[str, Condition] = {c.name: c for c in [
    Condition("no_gate", "audit", "block", "방어 없음 (Gate audit)"),
    Condition("gate_llm_judge", "enforce", "judge", "정책 Gate + 판정 LLM(룰북)", judge="llm"),
    Condition("gate_static_rules", "enforce", "judge", "정책 Gate + 룰북 정적 코드", judge="rules"),
]}


def get_condition(name: str) -> Condition:
    if name not in CONDITIONS:
        raise ValueError(f"unknown condition {name!r} (known: {', '.join(CONDITIONS)})")
    return CONDITIONS[name]


def make_runtime(condition: str | Condition, functions, *, suite_name: str, prompt: str, mode: str | None = None,
                 escalate: str | None = None, stats: dict | None = None, spec=None, **runtime_kwargs):
    """PolicyGateRuntime for one task under `condition`.

    mode / escalate override the condition (replay runs audit and enforce from the same condition; audit never calls
    the judge). `stats` collects per-run LLM usage of the ToolSpec extractor. Extra kwargs go to the runtime.
    """
    from agentgate.policy_gate.assembly import build

    cond = get_condition(condition) if isinstance(condition, str) else condition
    mode = mode or cond.mode
    escalate = escalate or cond.escalate
    if mode == "audit" and escalate == "judge":
        escalate = "block"
    runtime_cls, kwargs = build(cond, suite_name=suite_name, prompt=prompt, escalate=escalate, stats=stats)
    return runtime_cls(functions, mode=mode, escalate_action=escalate, prompt=prompt if spec is not None else None,
                       spec=spec, **kwargs, **runtime_kwargs)
