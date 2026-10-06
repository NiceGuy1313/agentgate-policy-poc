# CLAUDE.md — agentgate 작업 규칙

AgentDojo(v1.2.2, `agentdojo==0.1.35`) 위에서 결정론적 툴 호출 정책 엔진(P1–P9)을 검증하는 PoC다. 기준 문서는 `docs/policy_spec.md`, 진행 상황은 `docs/PROGRESS.md`다.

## 규칙

- agentdojo 패키지 소스는 수정하지 않는다. 확장은 서브클래스·래핑으로만 한다.
- 정책 판정 코드(src/agentgate/policy/)에는 LLM 호출을 넣지 않는다. LLM 호출은 src/agentgate/llm/ 아래 TaskSpec 추출기와 상향 판정기에만 둔다.
- TaskSpec 추출기의 입력은 사용자 프롬프트와 툴 목록(이름·설명)뿐이다. 툴 응답, ground truth, injection GOAL을 입력으로 넣지 않는다.
- 정책을 수정하면 pytest 전체와 scripts/replay_eval.py(생성 후)를 회귀 테스트로 다시 실행하고 이전 결과와 비교한다.
- 평가 수치를 바꾸기 위해 정책을 조정할 때는 먼저 변경안을 보고하고 승인을 받는다. 결과는 버전별(results/.../v1, v2)로 보존한다.
- 문서와 보고서에 쓰는 수치는 스크립트 출력에서만 가져온다. 추정치를 쓰지 않는다.
- 문서는 한국어 문어체로 쓰고, 기술 용어·식별자·논문명은 영어로 유지한다.
- 확인하지 못한 사실은 "확인되지 않음"으로 표기한다.
- 세션 종료 시 docs/PROGRESS.md에 완료 항목, 미해결 사항, 다음 단계를 갱신한다.

## 추가 규칙 (이 저장소 운영)

- API 키는 `.env`(git 제외)에만 둔다. 형식은 `.env.example`. 키를 커밋하지 않는다.
- 숫자가 들어간 보고서(`docs/stage1_catalog_report.md`, `docs/stage5_replay_report.md`, `docs/stage6_live_report.md`, `docs/final_report.md`)는 손으로 고치지 않고 `scripts/build_report.py`를 고친 뒤 재생성한다.
- `config/named_source_rules.yaml`의 `trusted_tools`에 툴을 추가하면 `tests/test_config_consistency.py`가 인젝션 벡터를 담지 않음을 확인해야 한다.

## 자주 쓰는 명령

```bash
. .venv/bin/activate
python -m pytest -q
python scripts/replay_eval.py --spec oracle --out results/replay/v1/oracle && python scripts/summarize_replay.py
python scripts/live_eval.py --agent scripted --spec oracle --conditions C0 C1 C2 C3 --out results/live/v1/scripted
python scripts/check_enforcement.py --run results/live/v1/scripted && python scripts/summarize_live.py --run results/live/v1/scripted
python scripts/build_report.py
python scripts/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```
