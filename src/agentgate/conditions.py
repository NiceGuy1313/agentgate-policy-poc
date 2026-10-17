"""실험 조건 등록부: 조건 이름 → (버전, Gate 모드, ESCALATE 처리). 런타임 조립은 각 버전의 `experiment` 모듈이 한다.

| 조건 | 버전 | 내용 |
|---|---|---|
| C0  | baseline | 방어 없음. Gate는 audit(판정만 기록, 차단 없음) |
| C3  | baseline | AgentDojo 내장 방어(spotlighting_with_delimiting 등), Gate는 audit. native만 |
| C1  | v1 | P1–P9, ESCALATE→BLOCK |
| C2  | v1 | P1–P9, ESCALATE→ALLOW |
| C4  | v1 | P1–P9, ESCALATE→판정 LLM v1 |
| C5  | v2 | P0+P1–P10, 판별기 horizon-base, ESCALATE→Trusted LLM v2 (HUMAN→BLOCK) |
| C5p | v2 | C5와 같고 판별기만 PIGuard |
| C6  | v3 | P0+P1–P9+P11·P12, ToolSpec, ESCALATE→판정 LLM v3 + 정책 문서 v3 |
| C7  | v3.1 | v3 + A1·B1·B2·C1·D2, ESCALATE→판정 LLM v3 + 정책 문서 v3.1 |
| C7n | v3.1 | C7과 같고 판정 LLM에 정책 문서를 주지 않음(ablation) |
| C8  | v3.1 | v3.1 Gate, 모든 effect 호출과 엔진이 ALLOW하지 않은 호출을 판정 LLM v3가 최종 판단(정책 문서 전체, 엔진 판정은 근거). P0 always_block만 판정 없이 BLOCK |
| C8n | v3.1 | C8과 같고 판정 LLM에 정책 문서를 주지 않음 |
| C8b | v3.1 | v3.1 Gate만, 판정 LLM 없음(ESCALATE→BLOCK). C8·C8n의 비교 기준 |

C0·C3은 버전과 무관한 기준선이다. 버전을 비교할 때는 같은 실행(같은 짝·공격·모델·시점)에 C0을 함께 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    name: str
    version: str          # baseline | v1 | v2 | v3
    mode: str             # audit | enforce
    escalate: str         # block | allow | judge
    description: str
    gate: str = "v1"      # policy configuration: v1 | v2 | v3 | v31
    judge: str | None = None   # v1 | v2 | v3 | v3-norules | v3-all | v3-all-norules (escalate == "judge")
    defense: str | None = None  # AgentDojo pipeline defense (C3)
    detector: str | None = None  # v2 detector model in config/v2/detector.yaml (None = its default)

    @property
    def gated(self) -> bool:
        """Enforcing conditions use the requested TaskSpec source; audit baselines record with the oracle spec."""
        return self.mode == "enforce"


CONDITIONS: dict[str, Condition] = {c.name: c for c in [
    Condition("C0", "baseline", "audit", "block", "방어 없음 (Gate audit)"),
    Condition("C3", "baseline", "audit", "block", "AgentDojo 내장 방어 (Gate audit)", defense="spotlighting_with_delimiting"),
    Condition("C1", "v1", "enforce", "block", "v1 Gate, ESCALATE→BLOCK"),
    Condition("C2", "v1", "enforce", "allow", "v1 Gate, ESCALATE→ALLOW"),
    Condition("C4", "v1", "enforce", "judge", "v1 Gate, ESCALATE→판정 LLM v1", judge="v1"),
    Condition("C5", "v2", "enforce", "judge", "v2 Gate(P0·P10, horizon-base), ESCALATE→Trusted LLM v2", gate="v2",
              judge="v2", detector="horizon-base"),
    Condition("C5p", "v2", "enforce", "judge", "v2 Gate(P0·P10, PIGuard), ESCALATE→Trusted LLM v2", gate="v2",
              judge="v2", detector="piguard"),
    Condition("C6", "v3", "enforce", "judge", "v3 Gate(P11·P12, ToolSpec), ESCALATE→판정 LLM v3", gate="v3", judge="v3"),
    Condition("C7", "v3", "enforce", "judge", "v3.1 Gate, ESCALATE→판정 LLM v3 + 정책 문서 v3.1", gate="v31", judge="v3"),
    Condition("C7n", "v3", "enforce", "judge", "v3.1 Gate, ESCALATE→판정 LLM v3(정책 문서 없음)", gate="v31",
              judge="v3-norules"),
    Condition("C8", "v3", "enforce", "judge", "v3.1 Gate, 모든 effect·엔진 플래그 호출→판정 LLM v3(정책 문서 전체, 최종 판단)",
              gate="v31", judge="v3-all"),
    Condition("C8n", "v3", "enforce", "judge", "C8과 같고 판정 LLM에 정책 문서를 주지 않음", gate="v31",
              judge="v3-all-norules"),
    Condition("C8b", "v3", "enforce", "block", "v3.1 Gate만(판정 LLM 없음), ESCALATE→BLOCK", gate="v31"),
]}


def get_condition(name: str) -> Condition:
    if name not in CONDITIONS:
        raise ValueError(f"unknown condition {name!r} (known: {', '.join(CONDITIONS)})")
    return CONDITIONS[name]


def conditions_of(version: str) -> list[str]:
    return [c.name for c in CONDITIONS.values() if c.version == version]


def make_runtime(condition: str | Condition, functions, *, suite_name: str, prompt: str, mode: str | None = None,
                 escalate: str | None = None, disabled: set[str] | None = None, stats: dict | None = None,
                 spec=None, **runtime_kwargs):
    """GatedRuntime for one task under `condition`.

    mode / escalate override the condition (replay runs audit and enforce from the same condition; audit never calls
    the judge). `stats` collects per-run LLM usage of version components (v3 ToolSpec). Extra kwargs go to the runtime.
    """
    cond = get_condition(condition) if isinstance(condition, str) else condition
    mode = mode or cond.mode
    escalate = escalate or cond.escalate
    if mode == "audit" and escalate == "judge":
        escalate = "block"
    if cond.gate == "v1":
        from agentgate.v1.experiment import build
    elif cond.gate == "v2":
        from agentgate.v2.experiment import build
    else:
        from agentgate.v3.experiment import build
    runtime_cls, kwargs = build(cond, suite_name=suite_name, prompt=prompt, escalate=escalate, disabled=disabled,
                                stats=stats)
    return runtime_cls(functions, mode=mode, escalate_action=escalate, prompt=prompt if spec is not None else None,
                       spec=spec, **kwargs, **runtime_kwargs)
