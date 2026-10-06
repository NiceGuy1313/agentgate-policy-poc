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
| 6 | 파이프라인 통합, 집행 무결성 점검. 대역 에이전트 C0–C3 전체 실행, 실제 모델 파일럿(Opus 5.5·Sonnet 5.5), Sonnet 5.5·GLM-5.3 표본(`important_instructions`), Ollama Cloud 17개 모델 `tool_knowledge` 탐침, gpt-oss:120b·mistral-large-3:675b `tool_knowledge` 파일럿 표본(각 468회). **본 실험: 같은 두 모델의 층화 표본(user task 97개 × injection task 3개 = 291쌍 + 정상 97개, C0·C1·C2·C4, 모델당 1,552회)과 신뢰구간·C0 대비 검정.** 집행 위반 0건 | `src/agentgate/{pipeline,agent_llm}.py`, `scripts/{make_strat_pairs,significance_live}.py`, `data/pairs/strat3_seed0.txt`, `results/live/v1/strat3_tk_*`, `docs/stage6_live_report.md` |
| 7 | 판정 LLM, 판정기 단독 평가(Sonnet 5.5, 583건), C4 표본 실행, 최종 보고서·그림·대시보드 | `src/agentgate/llm/judge.py`, `results/judge/v1/`, `docs/final_report.md` |

테스트: `python -m pytest -q` → 140 passed. 누적 Claude API 사용액 약 $23.43(정가 기준, `docs/final_report.md` 9절).

### 층화 표본 결과 요약 (`docs/final_report.md` 요약, `docs/stage6_live_report.md` 2.5절)

- ASR: gpt-oss:120b C0 35.1% → C1 4.5% / C2 8.9% / C4 5.2%, mistral-large-3:675b C0 63.9% → C1 8.2% / C2 17.9% / C4 7.2%. 모든 게이트 조건에서 C0 대비 감소가 유의하다(McNemar p < 1e-18).
- 정상 Utility: C0 대비 유의한 손실은 gpt-oss C1(−11.3%p, p=0.019)뿐이다. C4는 gpt-oss −5.2%p(p=0.3), mistral −4.1%p(p=0.48)로 유의하지 않다.
- 게이트로 인한 정상 작업 실패(차단 + 실패, C0에서는 성공): gpt-oss C1 11건 / C4 8건, mistral C1 12건 / C4 5건. 주로 P1(목적지 출처)이다.
- 판정 LLM이 공격 호출을 허용한 사례(d')는 0건이다. C1·C4에서 남은 공격 성공의 탐지 실패는 지목 출처 안의 공격 URL(slack injection_task_1·3)과 요청된 툴의 인자 오염(travel injection_task_0·2) 두 유형뿐이다.

## 미해결 사항

- **원본 `docs/policy_spec.md` 없음**: 지시서의 조건으로 v1 사양을 다시 썼다. 원본이 있으면 교체하고 Stage 1 대조를 다시 해야 한다.
- **실제 모델 실행은 층화 표본까지**: 본 실험은 291쌍 + 정상 97개다. 949쌍 전체와 C3(내장 방어)의 실제 모델 실행은 하지 않았다. 949쌍 결과와 같은 표에서 직접 비교할 수 없다. 이번 세션의 API 키는 세션 종료 후 폐기된다.
- **조건당 1회 실행**: 차단이 없는 정상 실행끼리도 조건 사이에 Utility가 gpt-oss 8.0%, mistral 12.8%의 짝에서 달랐다(모델 무작위성). 수 %p 수준의 Utility 차이는 통계적으로 확정하지 못하므로 게이트로 인한 실패 사례로 보완했다.
- **최신 모델은 내장 공격에 넘어가지 않음**: Opus 5.5·Sonnet 5.5·GLM-5.3은 `important_instructions`에 공격 시도 0건. 방어 효과는 `tool_knowledge`에 넘어가는 gpt-oss:120b·mistral-large-3:675b로 측정했다(`docs/stage6_live_report.md`). 적응형 공격(게이트를 아는 공격자)은 평가하지 않았다.
- **C1·C4에서 남은 공격 성공(탐지 실패 b)**: 사용자가 지목한 채널·웹페이지 안의 공격 URL을 보내거나 방문하는 경우(slack injection_task_1·3, P7)와 사용자가 요청한 일정·예약 툴에 공격 값이 섞인 경우(travel injection_task_0·2, P8·P9). 층화 표본에서도 같은 두 유형으로 확인됐다. 정책 수정은 승인 대상이므로 적용하지 않았다.
- **Opus 5.5 판정기 단독 평가는 중단**(모델 전환 결정). 캐시만 남아 있다.
- **oracle spec 검토 대기**: 97개 모두 `status: draft`. 애매함 21건, GT 대조 불일치 29건.
- **정책 수정 제안 v2 승인 대기**: `docs/stage5_replay_report.md` 7절의 6개 제안.

## 다음 단계

1. oracle spec 검토·확정, 위임형 태스크의 requested_ops 결정.
2. v2 제안 승인 여부 결정 → v2 재생 평가와 v1 비교.
3. 남은 탐지 실패 2유형(지명 웹페이지 인젝션, 요청된 툴의 인자 오염)에 대한 정책 보강안을 작성해 승인을 받는다.
4. 필요하면 949쌍 전체 실행 또는 층화 표본 반복 실행(새 키, `scripts/live_eval.py --agent llm --pairs @data/pairs/strat3_seed0.txt`). 반복은 결과 폴더를 회차별로 나눈다.
5. 정상 작업을 막은 P1 차단(2단계 출처 목적지)에 대한 수정안을 v2 제안과 함께 검토한다.
6. `scripts/build_report.py`로 보고서 갱신.
