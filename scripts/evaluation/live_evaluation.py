"""live 평가(RQ2): 한 실행 안에서 여러 조건으로 에이전트를 AgentDojo에 돌리고 전 과정을 기록한다.

조건(`agentgate.experiment.conditions`): no_gate 기준선, gate_llm_judge, gate_static_rules. 조건을 비교하려면 같은
실행에 기준선 no_gate를 함께 넣는다(같은 짝·공격·모델·시점). 다른 실행의 no_gate와 섞어 비교하지 않는다.

규모
  기본(--pairs, --pilot, --sample 없음): AgentDojo v1.2.2 전체 = 사용자 작업 97개(공격 없음) + 공격 쌍 949개
  --pairs @data/agentdojo/pairs/strat3_seed0.txt: 사용자 작업마다 공격 3개(층화 표본) = 97 + 291
  --pilot: banking 4 × 3

에이전트
  --agent llm       실제 LLM 에이전트 (키는 .env; AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL)
  --agent scripted  결정론적 대본 에이전트(가짜 LLM 서버, 키 불필요) - 모델 행동이 아니라 통합을 확인한다

프레임워크 (--framework, 기본값 native = AgentDojo pipeline)
  agentgate.harness.registry에 등록된 이름. native도 Adapter -> GateToolBridge -> Session 경로로 돈다.

실행 코드는 `agentgate.scenarios.runner`에 있고, 이 스크립트는 `python -m agentgate.scenarios.run`의 얇은 wrapper다.
차이는 기본값 두 가지다. TaskSpec 캐시를 <out>/taskspec_cache에 새로 만들고(live 추출), --injection-tasks가 없으면
모든 injection task와 공격 없는 run을 넣는다. 모든 프레임워크가 모든 조건으로 돈다. 정책 Gate를 우회한 툴 실행이
있는 run은 무효(valid: false)로 기록하고 점수를 집계에서 뺀다.

사용법
  python scripts/evaluation/live_evaluation.py --agent scripted --conditions no_gate gate_static_rules --out results/live/scripted
  python scripts/evaluation/live_evaluation.py --agent llm --conditions no_gate gate_llm_judge gate_static_rules \
      --attack important_instructions --out results/live/<run>
완료된 (condition, suite, user, injection) run은 건너뛰므로 중단된 실행을 이어서 할 수 있다.
"""

from __future__ import annotations

from agentgate.scenarios.run import main

if __name__ == "__main__":
    main(legacy=True)
