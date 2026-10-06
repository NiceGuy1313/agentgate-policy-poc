# docs 안내

각 문서가 무엇을 다루는지 한 줄씩 정리한다. 처음 읽는다면 1 → 2 → 3 순서를 권한다.

## 먼저 읽을 문서

1. **`conditions_and_attacks.md`**: 실험 조건 C0–C4의 뜻, 공격 방식(`important_instructions`, `tool_knowledge`), 공격 유형 A–H, 결과를 읽는 법.
2. **`gate_design.md`**: 게이트의 위치, 판정 입력(TaskSpec·출처), 정책 P1–P9, ESCALATE와 판정 LLM, 실제 판정 예, 다섯 단계 검증 흐름, 한계.
3. **`final_report.md`**: 전체 결과 요약과 결론(스크립트 생성).

## 설계·사양

- **`policy_spec.md`**: 정책 사양 v1(P1–P9, TaskSpec, 판정 원칙). 원본이 없어 지시서 조건으로 다시 썼다.
- **`policy_impl.md`**: 사양을 코드로 옮기며 내린 해석과 설정 값.
- **`design_decisions.md`**: 가로채기 지점, 출처 범주, Claude·Ollama 모델 사용 방식 등 설계 결정의 근거.
- **`agentdojo_api_notes.md`**: AgentDojo v1.2.2(`agentdojo==0.1.35`) API 확인 기록.

## 단계별 보고서 (숫자가 들어간 문서는 `scripts/build_report.py`로 재생성)

- **`stage1_catalog_report.md`**: user task 97개, injection task 35개 카탈로그, 유형 레이블, 인자 출처 분석.
- **`stage4_oracle_review.md`**: oracle TaskSpec 97개의 검토표와 정답 궤적 대조.
- **`stage4_spec_comparison.md`**: LLM이 추출한 TaskSpec과 oracle 비교.
- **`stage5_replay_report.md`**: 정답 궤적 재생 평가(탐지율, 저지율, 오탐, ablation, 실패 분석, v2 제안).
- **`stage6_live_report.md`**: 대역 에이전트와 실제 모델의 파이프라인 실행, 집행 무결성, 우회 경로 분석.

## 진행 상황

- **`PROGRESS.md`**: 완료 항목, 미해결 사항, 다음 단계.
