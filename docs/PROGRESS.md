# PROGRESS

최종 갱신: 2026-10-06. 정책 설정 버전 v1 (`config/policies.yaml`). 벤치마크 `v1.2.2`, `agentdojo==0.1.35`. LLM: `claude-sonnet-5-5`(기본), `claude-opus-5-5`(비교).

## 완료

| Stage | 내용 | 산출물 |
|---|---|---|
| 0 | 프로젝트 골격, AgentDojo API 확인(a~k), 스모크 테스트(4 suite utility True), CLAUDE.md | `pyproject.toml`, `docs/agentdojo_api_notes.md`, `scripts/smoke_test.py`, `CLAUDE.md` |
| 1 | 카탈로그 추출(user 97 / injection 35), 유형 레이블과 모순 점검(0건), 준비/효과 호출 구분, 인자 출처 분석, 공격자 목적지 점검 | `data/catalog/*`, `docs/stage1_catalog_report.md` |
| 2 | TaskSpec, 정규화, ValueIndex·출처 판정, 세션 상태, GatedRuntime(audit/enforce, 지연 기록), 우회 경로 없음 테스트 | `src/agentgate/{spec,normalize,trace,session,runtime}.py`, `docs/design_decisions.md` |
| 3 | P1–P9 정책 엔진, 설정 분리, 정책별 양성·음성 테스트 | `src/agentgate/policy/`, `config/policies.yaml`, `docs/policy_impl.md` |
| 4 | oracle TaskSpec 97개 초안(프롬프트만 근거), 검토표, GT 대조, LLM 추출 97개 × 2개 모델과 oracle 비교 | `data/specs/oracle/`, `data/specs/llm/`, `docs/stage4_oracle_review.md`, `docs/stage4_spec_comparison.md` |
| 5 | 정답 궤적 재생 평가(949쌍 audit/enforce; oracle·LLM spec 2종), 지표·분해·ablation·실패 분석, 수정 제안 v2 | `results/replay/v1/`, `docs/stage5_replay_report.md` |
| 6 | 파이프라인 통합, 집행 무결성 점검. 대역 에이전트 C0–C3 전체 실행, 실제 모델 파일럿(Opus 5.5·Sonnet 5.5), Sonnet 5.5 표본(C0·C1·C2·C4, 468회). 위반 0건 | `src/agentgate/{pipeline,agent_llm}.py`, `results/live/v1/`, `docs/stage6_live_report.md` |
| 7 | 판정 LLM, 판정기 단독 평가(Sonnet 5.5, 583건), C4 표본 실행, 최종 보고서·그림·대시보드 | `src/agentgate/llm/judge.py`, `results/judge/v1/`, `docs/final_report.md` |

테스트: `python -m pytest -q` → 131 passed. 이번 세션의 API 사용액 약 $23(정가 기준, `docs/final_report.md` 9절).

## 미해결 사항

- **원본 `docs/policy_spec.md` 없음**: 지시서의 조건으로 v1 사양을 다시 썼다. 원본이 있으면 교체하고 Stage 1 대조를 다시 해야 한다.
- **실제 모델 실행은 표본까지**: 사용자 결정으로 Sonnet 5.5 표본(suite별 user task 3개)만 실행했다. 전체 949쌍, C3(내장 방어)의 실제 모델 실행, 다른 attack으로의 일반화 점검(Stage 7 작업 4)은 하지 않았다. 이번 세션의 API 키는 세션 종료 후 폐기된다.
- **실제 모델이 공격에 거의 넘어가지 않음**: Opus 5.5·Sonnet 5.5 모두 `important_instructions`에 공격 시도 0건. 실제 모델에서의 방어 효과를 보려면 다른(적응형) attack이 필요하다.
- **Opus 5.5 판정기 단독 평가는 중단**(모델 전환 결정). 캐시만 남아 있다.
- **oracle spec 검토 대기**: 97개 모두 `status: draft`. 애매함 21건, GT 대조 불일치 29건.
- **정책 수정 제안 v2 승인 대기**: `docs/stage5_replay_report.md` 7절의 6개 제안.

## 다음 단계

1. oracle spec 검토·확정, 위임형 태스크의 requested_ops 결정.
2. v2 제안 승인 여부 결정 → v2 재생 평가와 v1 비교.
3. 실제 모델에서 방어 효과를 보려면 다른 attack(예: `tool_knowledge`, `important_instructions_no_names`)으로 C0 표본부터 실행해 공격 시도율을 확인한다.
4. 필요하면 전체 949쌍 실행(새 키, `scripts/live_eval.py --agent llm`; 표본 기준 태스크당 약 $0.04).
5. `scripts/build_report.py`로 보고서 갱신.
