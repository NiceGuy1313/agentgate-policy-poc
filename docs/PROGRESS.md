# PROGRESS

최종 갱신: 2026-10-06. 정책 설정 버전 v1 (`config/policies.yaml`). 벤치마크 `v1.2.2`, `agentdojo==0.1.35`.

## 완료

| Stage | 내용 | 산출물 |
|---|---|---|
| 0 | 프로젝트 골격, AgentDojo API 확인(a~k), 스모크 테스트(4 suite utility True), CLAUDE.md | `pyproject.toml`, `docs/agentdojo_api_notes.md`, `scripts/smoke_test.py`, `CLAUDE.md` |
| 1 | 카탈로그 추출(user 97 / injection 35), 유형 레이블과 모순 점검(0건), 준비/효과 호출 구분, 인자 출처 분석, 공격자 목적지 점검 | `data/catalog/*`, `docs/stage1_catalog_report.md` |
| 2 | TaskSpec, 정규화, ValueIndex·출처 판정, 세션 상태, GatedRuntime(audit/enforce, 지연 기록), 우회 경로 없음 테스트 | `src/agentgate/{spec,normalize,trace,session,runtime}.py`, `docs/design_decisions.md` |
| 3 | P1–P9 정책 엔진, 설정 분리, 정책별 양성·음성 테스트 | `src/agentgate/policy/`, `config/policies.yaml`, `docs/policy_impl.md` |
| 4 | oracle TaskSpec 97개 초안(프롬프트만 근거), 검토표, GT 대조, LLM 추출기·비교 스크립트(코드) | `data/specs/oracle/`, `docs/stage4_oracle_review.md`, `src/agentgate/llm/extractor.py` |
| 5 | 정답 궤적 재생 평가(949쌍 audit/enforce), 지표·분해·ablation·실패 분석, 수정 제안 v2 | `results/replay/v1/oracle/`, `docs/stage5_replay_report.md` |
| 6 | 파이프라인 통합(TaskSpecElement), live 평가 스크립트, 집행 무결성 점검, 대역 에이전트로 C0–C3 전체 실행(위반 0건) | `src/agentgate/pipeline.py`, `results/live/v1/scripted/`, `docs/stage6_live_report.md` |
| 7 | 판정 LLM(코드·프롬프트·캐시), 판정기 단독 평가 스크립트, 최종 보고서·그림·대시보드 생성기 | `src/agentgate/llm/judge.py`, `scripts/judge_eval.py`, `scripts/build_report.py`, `docs/final_report.md` |

테스트: `python -m pytest -q` → 128 passed.

## 미해결 사항

- **원본 `docs/policy_spec.md` 없음**: 지시서의 조건으로 v1 사양을 다시 썼다. 원본이 있으면 교체하고 Stage 1 대조(2절 수치)를 다시 해야 한다.
- **API 키 없음**: 다음을 실행하지 못했다. LLM TaskSpec 추출과 oracle 비교(Stage 4 작업 4·5), llm spec 재생(Stage 5), 실제 LLM 에이전트 파일럿·전체 실행(Stage 6 작업 3·4), 판정기 단독 평가·C4(Stage 7 작업 2·3), 다른 attack으로의 일반화 점검(Stage 7 작업 4). 코드와 명령은 준비되어 있다(README).
- **oracle spec 검토 대기**: 97개 모두 `status: draft`. 애매함 표시 21건, GT 대조 불일치 29건(`docs/stage4_oracle_review.md`). 사람이 검토해 `status: reviewed`로 바꿔야 한다.
- **정책 수정 제안 v2 승인 대기**: `docs/stage5_replay_report.md` 7절의 6개 제안. 승인 후 별도 세션에서 적용하고 `results/replay/v2`로 재실행한다.
- 실제 에이전트 행동(차단 후 재시도·우회), 비용(토큰·지연)은 대역 에이전트로 측정할 수 없었다.

## 다음 단계

1. oracle spec 검토·확정 (`status: reviewed`), 위임형 태스크의 requested_ops 결정.
2. `.env`에 키 입력 → `scripts/extract_specs.py`, `scripts/compare_specs.py`, `scripts/replay_eval.py --spec llm:<model>`.
3. Stage 6 파일럿(`--pilot --conditions C0 C1`) → 비용 추정 보고 → 전체 실행 범위 승인 → 전체 실행.
4. 판정기 단독 평가(`scripts/judge_eval.py`) → C4 비용 추정 → 승인 후 C4 실행.
5. v2 제안 승인 여부 결정 → v2 재생 평가와 v1 비교.
6. `scripts/build_report.py`로 최종 보고서 갱신.
