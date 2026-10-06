# PROGRESS

최종 갱신: 2026-10-06. 정책 설정 버전 v1 (`config/policies.yaml`). 벤치마크 `v1.2.2`, `agentdojo==0.1.35`. LLM: `claude-sonnet-5-5`(TaskSpec 추출·판정), 에이전트 모델은 Claude 5.5 계열과 Ollama Cloud 모델(`glm-5.3`, `gpt-oss:120b`, `mistral-large-3:675b` 등).

## 완료

| Stage | 내용 | 산출물 |
|---|---|---|
| 0 | 프로젝트 골격, AgentDojo API 확인(a~k), 스모크 테스트(4 suite utility True), CLAUDE.md | `pyproject.toml`, `docs/agentdojo_api_notes.md`, `scripts/smoke_test.py`, `CLAUDE.md` |
| 1 | 카탈로그 추출(user 97 / injection 35), 유형 레이블과 모순 점검(0건), 준비/효과 호출 구분, 인자 출처 분석, 공격자 목적지 점검 | `data/catalog/*`, `docs/stage1_catalog_report.md` |
| 2 | TaskSpec, 정규화, ValueIndex·출처 판정, 세션 상태, GatedRuntime(audit/enforce, 지연 기록), 우회 경로 없음 테스트 | `src/agentgate/{spec,normalize,trace,session,runtime}.py`, `docs/design_decisions.md` |
| 3 | P1–P9 정책 엔진, 설정 분리, 정책별 양성·음성 테스트 | `src/agentgate/policy/`, `config/policies.yaml`, `docs/policy_impl.md` |
| 4 | oracle TaskSpec 97개 초안(프롬프트만 근거), 검토표, GT 대조, LLM 추출 97개 × 2개 모델과 oracle 비교 | `data/specs/oracle/`, `data/specs/llm/`, `docs/stage4_oracle_review.md`, `docs/stage4_spec_comparison.md` |
| 5 | 정답 궤적 재생 평가(949쌍 audit/enforce; oracle·LLM spec 2종), 지표·분해·ablation·실패 분석, 수정 제안 v2 | `results/replay/v1/`, `docs/stage5_replay_report.md` |
| 6 | 파이프라인 통합, 집행 무결성 점검. 대역 에이전트 C0–C3 전체 실행, 실제 모델 파일럿(Opus 5.5·Sonnet 5.5), Sonnet 5.5·GLM-5.3 표본(`important_instructions`), Ollama Cloud 17개 모델 `tool_knowledge` 탐침, gpt-oss:120b·mistral-large-3:675b `tool_knowledge` 표본(C0·C1·C2·C4, 각 468회). 집행 위반 0건 | `src/agentgate/{pipeline,agent_llm}.py`, `results/live/v1/`, `docs/stage6_live_report.md` |
| 7 | 판정 LLM, 판정기 단독 평가(Sonnet 5.5, 583건), C4 표본 실행, 최종 보고서·그림·대시보드 | `src/agentgate/llm/judge.py`, `results/judge/v1/`, `docs/final_report.md` |

테스트: `python -m pytest -q` → 138 passed. 이번 세션의 API 사용액 약 $23(정가 기준, `docs/final_report.md` 9절).

## 미해결 사항

- **원본 `docs/policy_spec.md` 없음**: 지시서의 조건으로 v1 사양을 다시 썼다. 원본이 있으면 교체하고 Stage 1 대조를 다시 해야 한다.
- **실제 모델 실행은 표본까지**: 표본(105쌍 + 정상 12개)만 실행했다. 전체 949쌍과 C3(내장 방어)의 실제 모델 실행은 하지 않았다. 이번 세션의 API 키는 세션 종료 후 폐기된다.
- **최신 모델은 내장 공격에 넘어가지 않음**: Opus 5.5·Sonnet 5.5·GLM-5.3은 `important_instructions`에 공격 시도 0건. 방어 효과는 `tool_knowledge`에 넘어가는 gpt-oss:120b·mistral-large-3:675b로 측정했다(`docs/stage6_live_report.md`). 적응형 공격(게이트를 아는 공격자)은 평가하지 않았다.
- **C1·C4에서 남은 공격 성공(탐지 실패 b)**: 사용자가 지명한 웹페이지 안의 인젝션(slack user_task_17)과 사용자가 요청한 일정·예약 툴에 공격 값이 섞인 경우(travel user_task_4·6). 정책 수정은 승인 대상이므로 적용하지 않았다.
- **Opus 5.5 판정기 단독 평가는 중단**(모델 전환 결정). 캐시만 남아 있다.
- **oracle spec 검토 대기**: 97개 모두 `status: draft`. 애매함 21건, GT 대조 불일치 29건.
- **정책 수정 제안 v2 승인 대기**: `docs/stage5_replay_report.md` 7절의 6개 제안.

## 다음 단계

1. oracle spec 검토·확정, 위임형 태스크의 requested_ops 결정.
2. v2 제안 승인 여부 결정 → v2 재생 평가와 v1 비교.
3. 남은 탐지 실패 2유형(지명 웹페이지 인젝션, 요청된 툴의 인자 오염)에 대한 정책 보강안을 작성해 승인을 받는다.
4. 필요하면 전체 949쌍 실행(새 키, `scripts/live_eval.py --agent llm`; 표본 기준 태스크당 약 $0.04).
5. `scripts/build_report.py`로 보고서 갱신.
