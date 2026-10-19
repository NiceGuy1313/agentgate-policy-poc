# CLAUDE.md — agentgate 작업 규칙

AgentDojo(v1.2.2, `agentdojo==0.1.35`) 위에서 결정론적 툴 호출 정책 엔진(P1–P9)을 검증하는 PoC다. 기준 문서는 `docs/policy_spec.md`, 구조는 `docs/architecture.md`, 결과는 `docs/final_report.md`다.

## 규칙

- agentdojo 패키지 소스는 수정하지 않는다. 확장은 서브클래스·래핑으로만 한다.
- 정책 판정 코드(src/agentgate/gate/, src/agentgate/v*/policies.py)에는 LLM 호출을 넣지 않는다. LLM 호출은 TaskSpec 추출기(src/agentgate/llm/), 판정 LLM(src/agentgate/v*/judge.py), ToolSpec 추출기(src/agentgate/v3/toolspec.py)에만 둔다. 예외는 평가 대상 에이전트(src/agentgate/agents/)다.
- TaskSpec 추출기의 입력은 사용자 프롬프트와 툴 목록(이름·설명)뿐이다. 툴 응답, ground truth, injection GOAL을 입력으로 넣지 않는다.
- 정책을 수정하면 pytest 전체와 scripts/evaluation/replay_evaluation.py를 회귀 테스트로 다시 실행하고 이전 결과와 비교한다.
- 평가 수치를 바꾸기 위해 정책을 조정할 때는 먼저 변경안을 보고하고 승인을 받는다. 결과는 버전별(results/<version>/...)로 보존한다.
- 문서와 보고서에 쓰는 수치는 스크립트 출력에서만 가져온다. 추정치를 쓰지 않는다.
- 문서는 한국어 문어체로 쓰고, 기술 용어·식별자·논문명은 영어로 유지한다.
- 확인하지 못한 사실은 "확인되지 않음"으로 표기한다.
- 문서는 핵심만 짧게 쓴다. 새 문서를 늘리기보다 기존 세 문서와 README를 갱신한다.

## 추가 규칙 (이 저장소 운영)

- API 키는 `.env`(git 제외)에만 둔다. 형식은 `.env.example`. 키를 커밋하지 않는다.
- 숫자가 들어간 보고서(`docs/final_report.md`)와 `results/figures/`, `results/dashboard.html`은 손으로 고치지 않고 `scripts/reporting/build_report.py`를 고친 뒤 재생성한다.
- `config/agentdojo/named_source_rules.yaml`의 `trusted_tools`에 툴을 추가하면 `tests/test_config_consistency.py`가 인젝션 벡터를 담지 않음을 확인해야 한다.

## 자주 쓰는 명령

```bash
. .venv/bin/activate
python -m pytest -q
python scripts/evaluation/replay_evaluation.py --condition C1 --spec oracle --out results/v1/replay/oracle && python scripts/analysis/summarize_replay.py --run results/v1/replay/oracle
python scripts/analysis/check_enforcement.py --run results/<version>/live/<run> && python scripts/analysis/summarize_live.py --run results/<version>/live/<run>
python scripts/reporting/build_report.py
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

## 버전 구조 (v1·v3)

- 코드는 `src/agentgate/gate/`(공통 Gate), `agentdojo/`(AgentDojo 테스트), `agents/`(평가 대상 에이전트), `v1/`·`v3/`(버전별 정책·판정 LLM·런타임 확장)로 나뉜다. 실험 조건은 `src/agentgate/conditions.py`(C0–C9n)다.
- 버전별 차이는 해당 버전 패키지와 `config/<version>/`, `prompts/<version>/`에만 둔다. 공통 Gate나 v1 클래스에 다른 버전 분기를 넣지 않고 hook·서브클래스로 바꾼다.
- 한 버전의 설정·프롬프트를 고칠 때 다른 버전 파일을 함께 고치지 않는다. 공유해야 하면 공통 위치(`gate/`, `config/agentdojo/`)로 옮기고 모든 버전의 회귀를 다시 확인한다.
- v2(IPI detector + Trusted LLM)는 2026-10-19 미팅 결정으로 중단하고 `archive/v2.bak/`에 보관한다. 현재 코드·테스트·조건(C5·C5p)에서 v2를 import하거나 되살리지 않는다.
- 결과는 `results/<version>/`, LLM 캐시는 `data/<version>/cache/`에 둔다. 버전 비교는 같은 실행 안의 기준선 C0과 한다.
- 오래 걸리는 평가(재생 전체, live)는 백그라운드로 돌리고 로그를 `logs/`에 남긴다.
- 클라우드 세션은 SessionStart hook(`scripts/cloud/setup.sh`)이 `.venv`를 준비한다. 시작하면 `python scripts/cloud/check_env.py`부터 돌리고, 실패 항목이 있으면 진행 전에 보고한다.
