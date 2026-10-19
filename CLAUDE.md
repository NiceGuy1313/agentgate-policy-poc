# CLAUDE.md — agentgate 작업 규칙

LLM 에이전트의 effect 툴 호출을 실행 직전에 룰북(R1–R10) 기준으로 판정하는 정책 Gate다. 평가 시나리오는 AgentDojo(v1.2.2, `agentdojo==0.1.35`)다. 기준 문서는 `docs/policy_spec.md`, 구조는 `docs/architecture.md`, 결과는 `results/RESULTS.md`다.

## 규칙

- agentdojo 패키지 소스는 수정하지 않는다. 확장은 서브클래스·래핑으로만 한다.
- 판정 코드(src/agentgate/core/, src/agentgate/policy_gate/rule_checker.py)에는 LLM 호출을 넣지 않는다. LLM 호출은 TaskSpec 추출기(src/agentgate/llm/), 판정 LLM(src/agentgate/policy_gate/llm_judge.py), ToolSpec 추출기(src/agentgate/policy_gate/toolspec.py)에만 둔다. 예외는 평가 대상 에이전트(src/agentgate/agents/)다.
- TaskSpec 추출기의 입력은 사용자 프롬프트와 툴 목록(이름·설명)뿐이다. 툴 응답, ground truth, injection GOAL을 입력으로 넣지 않는다.
- 정책을 수정하면 pytest 전체와 scripts/evaluation/replay_evaluation.py를 회귀 테스트로 다시 실행하고 이전 결과와 비교한다.
- 평가 수치를 바꾸기 위해 정책을 조정할 때는 먼저 변경안을 보고하고 승인을 받는다.
- 문서와 보고서에 쓰는 수치는 스크립트 출력에서만 가져온다. 추정치를 쓰지 않는다.
- 문서는 한국어 문어체로 쓰고, 기술 용어·식별자·논문명은 영어로 유지한다.
- 확인하지 못한 사실은 "확인되지 않음"으로 표기한다.
- 문서는 핵심만 짧게 쓴다. 새 문서를 늘리기보다 기존 문서와 README를 갱신한다.

## 추가 규칙 (이 저장소 운영)

- API 키는 `.env`(git 제외)에만 둔다. 형식은 `.env.example`. 키를 커밋하지 않는다.
- `results/RESULTS.md`는 손으로 고치지 않고 `scripts/analysis/summarize_results.py`·`scripts/evaluation/replay_contrast.py`를 고친 뒤 재생성한다.
- `config/agentdojo/named_source_rules.yaml`의 `trusted_tools`에 툴을 추가하면 `tests/test_config_consistency.py`가 인젝션 벡터를 담지 않음을 확인해야 한다.

## 자주 쓰는 명령

```bash
. .venv/bin/activate
python -m pytest -q
python scripts/evaluation/replay_evaluation.py --condition gate_static_rules --spec oracle --out results/replay/oracle
python scripts/analysis/check_enforcement.py --run results/live/<run> && python scripts/analysis/summarize_live.py --run results/live/<run>
python scripts/analysis/summarize_results.py --run results/live/<run>
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

## 구조

- 코드는 `src/agentgate/core/`(Gate 공통부, 단일 집행 지점 `GatedRuntime`), `policy_gate/`(룰북 정적 검사기·판정 LLM·ToolSpec·`PolicyGateRuntime`), `experiment/conditions.py`(조건 `no_gate`·`gate_llm_judge`·`gate_static_rules`), `scenarios/`(AgentDojo), `agents/`(평가 대상 에이전트), `adapters/`(프레임워크 어댑터), `harness/`로 나뉜다.
- 설정은 `config/gate.yaml`(Gate 공통), `config/rulebook.yaml`·`config/tool_args.yaml`(룰북), `config/agentdojo/`(AgentDojo 바인딩)이다. 공통 Gate에 판정 방식 분기를 넣지 않고 hook·서브클래스로 바꾼다.
- AgentDojo 고유 값(툴 이름, 사용자 계정 등)은 `config/agentdojo/`와 `scenarios/`에만 둔다.
- v1(P1–P9)은 `archive/v1.bak/`, v2(IPI detector + Trusted LLM)는 `archive/v2.bak/`에 보관한다. 현재 코드·테스트·조건에서 보관본을 import하거나 되살리지 않는다.
- LLM 캐시는 `data/cache/{taskspec,toolspec,judge}/`(git 제외)에 둔다. 조건 비교는 같은 실행 안의 기준선 `no_gate`와 한다.
- 오래 걸리는 평가(재생 전체, live)는 백그라운드로 돌리고 로그를 `logs/`(git 제외)에 남긴다.
- 클라우드 세션은 SessionStart hook(`scripts/cloud/setup.sh`)이 `.venv`를 준비한다. 시작하면 `python scripts/cloud/check_env.py`부터 돌리고, 실패 항목이 있으면 진행 전에 보고한다.
