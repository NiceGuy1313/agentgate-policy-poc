# CLAUDE.md — agentgate 작업 규칙

AgentDojo(v1.2.2, `agentdojo==0.1.35`) 위에서 결정론적 툴 호출 정책 엔진(P1–P9)을 검증하는 PoC다. 기준 문서는 `docs/policy_spec.md`, 구조는 `docs/architecture.md`, 결과는 `docs/final_report.md`다.

## 규칙

- agentdojo 패키지 소스는 수정하지 않는다. 확장은 서브클래스·래핑으로만 한다.
- 정책 판정 코드(src/agentgate/policy/)에는 LLM 호출을 넣지 않는다. LLM 호출은 src/agentgate/llm/ 아래 TaskSpec 추출기와 판정 LLM에만 둔다. 예외는 평가 대상 에이전트(src/agentgate/agents/)다.
- TaskSpec 추출기의 입력은 사용자 프롬프트와 툴 목록(이름·설명)뿐이다. 툴 응답, ground truth, injection GOAL을 입력으로 넣지 않는다.
- 정책을 수정하면 pytest 전체와 scripts/evaluation/replay_evaluation.py를 회귀 테스트로 다시 실행하고 이전 결과와 비교한다.
- 평가 수치를 바꾸기 위해 정책을 조정할 때는 먼저 변경안을 보고하고 승인을 받는다. 결과는 버전별(results/.../v1, v2)로 보존한다.
- 문서와 보고서에 쓰는 수치는 스크립트 출력에서만 가져온다. 추정치를 쓰지 않는다.
- 문서는 한국어 문어체로 쓰고, 기술 용어·식별자·논문명은 영어로 유지한다.
- 확인하지 못한 사실은 "확인되지 않음"으로 표기한다.
- 문서는 핵심만 짧게 쓴다. 새 문서를 늘리기보다 기존 세 문서와 README를 갱신한다.

## 추가 규칙 (이 저장소 운영)

- API 키는 `.env`(git 제외)에만 둔다. 형식은 `.env.example`. 키를 커밋하지 않는다.
- 숫자가 들어간 보고서(`docs/final_report.md`)와 `results/figures/`, `results/dashboard.html`은 손으로 고치지 않고 `scripts/reporting/build_report.py`를 고친 뒤 재생성한다.
- `config/named_source_rules.yaml`의 `trusted_tools`에 툴을 추가하면 `tests/test_config_consistency.py`가 인젝션 벡터를 담지 않음을 확인해야 한다.

## 자주 쓰는 명령

```bash
. .venv/bin/activate
python -m pytest -q
python scripts/evaluation/replay_evaluation.py --spec oracle --out results/replay/v1/oracle && python scripts/analysis/summarize_replay.py --run results/replay/v1/oracle
python scripts/analysis/check_enforcement.py --run results/live/v1/<run> && python scripts/analysis/summarize_live.py --run results/live/v1/<run>
python scripts/reporting/build_report.py
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

## v2 작업 (판별기 + Trusted LLM)

- 현재 작업 명세는 `명세서.md`다. 단계(Phase 0–4) 순서대로 진행하고, 각 단계의 완료 기준을 확인한 뒤 다음으로 넘어간다.
- 클라우드 세션은 SessionStart hook(`scripts/cloud/setup.sh`)이 `.venv`를 준비한다. 시작하면 `python scripts/cloud/check_env.py`부터 돌리고, 실패 항목이 있으면 진행 전에 보고한다.
- 판별기 코드는 `src/agentgate/detector/`, 설정은 `config/detector.yaml`이다. 판별기는 툴 응답에만 돌리고, 정책 코드에는 그 결과(라벨)만 넘긴다.
- `GatedRuntime(detector=None)`과 v1 설정의 결과는 v1과 같아야 한다. v2 결과는 `results/*/v2/`, `results/detector/v1/`에 둔다.
- 오래 걸리는 평가(재생 전체, live)는 백그라운드로 돌리고 로그를 `logs/`에 남긴다.
