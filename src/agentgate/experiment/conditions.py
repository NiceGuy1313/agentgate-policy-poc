"""실험 조건 등록부: 조건 이름 → (Gate 모드, 판정기 이름). `make_runtime`이 조건 하나의 PolicyGateRuntime을 만든다.

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
    mode: str                  # audit | enforce
    judge: str | None          # judge registry name (`agentgate.policy_gate.judge.JUDGES`); None: no judge, no ToolSpec
    description: str

    @property
    def gated(self) -> bool:
        """Enforcing conditions use the requested TaskSpec source; the audit baseline only records."""
        return self.mode == "enforce"


CONDITIONS: dict[str, Condition] = {}


def register_condition(cond: Condition) -> Condition:
    """Add (or replace) a condition by name."""
    CONDITIONS[cond.name] = cond
    return cond


for _cond in (Condition("no_gate", "audit", None, "방어 없음 (Gate audit)"),
              Condition("gate_llm_judge", "enforce", "llm", "정책 Gate + 판정 LLM(룰북)"),
              Condition("gate_static_rules", "enforce", "rules", "정책 Gate + 룰북 정적 코드")):
    register_condition(_cond)


def get_condition(name: str) -> Condition:
    if name not in CONDITIONS:
        raise ValueError(f"unknown condition {name!r} (known: {', '.join(CONDITIONS)})")
    return CONDITIONS[name]


def make_runtime(condition: str | Condition, functions, *, suite_name: str, prompt: str, mode: str | None = None,
                 stats: dict | None = None, spec=None, **runtime_kwargs):
    """PolicyGateRuntime for one task under `condition`.

    `mode` overrides the condition's (replay runs audit and enforce from the same condition; audit never calls the
    judge). A condition with a judge labels every response with a ToolSpec; `stats` collects the extractor's LLM usage.
    Extra kwargs go to the runtime.
    """
    from agentgate.policy_gate import assembly
    from agentgate.policy_gate.judge import make_judge
    from agentgate.policy_gate.runtime import PolicyGateRuntime

    cond = get_condition(condition) if isinstance(condition, str) else condition
    mode = mode or cond.mode
    kwargs = {}
    if cond.judge is not None:   # no_gate: record calls only, no ToolSpec extraction, no judge
        kwargs["toolspec"] = assembly.toolspec_labeller(suite_name, stats)
        if mode == "enforce":
            kwargs["judge"] = make_judge(cond.judge)
    return PolicyGateRuntime(functions, mode=mode, prompt=prompt if spec is not None else None, spec=spec,
                             **kwargs, **runtime_kwargs)
