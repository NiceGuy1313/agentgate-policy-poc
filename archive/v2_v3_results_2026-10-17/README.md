# v2·v3 실험 결과 문서 백업 (2026-10-17)

v2(판별기 + Trusted LLM)와 v3·v3.1(ToolSpec + 정책 문서 + 판정 LLM) 실험을 다시 하기 전에, 그 시점의 결과 문서만 보존한다. 결과 데이터(jsonl, runs.tar.gz, summary.json)와 LLM 응답 캐시는 저장소에서 지웠다. 원본은 커밋 `848af692`에 있다.

이 문서의 수치는 다음 문제가 있는 실험 설정에서 나왔다. 재실험 결과와 섞어 쓰지 않는다.

- v3 작업이 v1과 공유하던 TaskSpec 추출 프롬프트(`prompts/extract_taskspec.md`)를 고쳐, 이후 v1 live의 LLM TaskSpec도 v3 프롬프트로 추출되었다.
- v2·v3 설정은 실행 시점에 v1 설정을 복사·수정해 만들었고, 정책 코드(`rules.py`)·런타임에 버전 분기가 섞여 있었다.
- 버전 간 비교 일부(짝 A)는 서로 다른 시점·실행의 C0–C4(v1), C5(v2), C6(v3) 결과를 묶었다.
- live는 전체 949 공격 쌍이 아니라 사용자 작업마다 공격 3개를 뽑은 층화 표본(97 + 291)이고, 공격은 `tool_knowledge`였다.

| 파일 | 원본 경로 |
|---|---|
| `v2_README.md`, `v2_RESULTS.md` | `v2_detector_trusted_llm/` |
| `v3_README.md`, `v3_RESULTS.md` | `v3_trusted_llm_policy_doc/` |
| `final_report.md` | `docs/final_report.md` (6절이 v2–v3.1) |
| `architecture.md`, `policy_spec.md` | `docs/` (v2–v3.1 반영본) |
