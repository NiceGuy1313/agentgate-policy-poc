# v3·v3.1: Trusted LLM + 정책 문서 (live 조건 C6, C7, C7n)

v3·v3.1 구성에 해당하는 파일을 저장소 루트의 같은 상대 경로에서 복사한 참고용 사본이다. 실행과 테스트는 루트의 원본으로 한다(`pytest`는 `tests/`만 수집한다). 원본을 고치면 이 사본은 갱신되지 않는다.

| 구성 | 파일 |
|---|---|
| 정책 문서(규칙 R1–R8, J1–J7, 트리거 인덱스) | `config/policy_rules_v3.yaml`, `config/policy_rules_v31.yaml` |
| 판정 LLM v3 | `src/agentgate/llm/trusted_judge_v3.py` (`use_rules=False`이면 정책 문서 없이 판정, C7n) |
| ToolSpec 추출기(Haiku) | `src/agentgate/llm/toolspec_extractor.py`, `prompts/extract_toolspec.md` |
| TaskSpec 추출기 | `src/agentgate/llm/taskspec_extractor.py`, `prompts/extract_taskspec.md`, `src/agentgate/task_spec.py` |
| 정책 P11·P12와 v3.1 플래그 | `src/agentgate/policy/rules.py`의 `InstructedActionPolicy`, `RequestedActionPolicy` |
| 출처·ToolSpec 요청 대조 | `src/agentgate/provenance.py` |
| 사용자 고정 설정(P0) | `config/user_policy.yaml` |
| ToolSpec 연결부 | `src/agentgate/runtime.py` |
| Anthropic 호출·prompt caching | `src/agentgate/llm/client.py` |
| 정책 문서 근거(ATR 분류), ToolSpec 품질 평가 | `scripts/analysis/analyze_atr_corpora.py`, `scripts/evaluation/toolspec_evaluation.py` |
| 테스트 | `tests/policy/test_p11_p12.py`, `tests/policy/test_v31.py` |

`rules.py`, `runtime.py`, `client.py`는 v2와 공유하는 파일이라 v2 코드도 들어 있다. v3부터 판별기는 쓰지 않는다.

재현(루트에서): `python scripts/evaluation/replay_evaluation.py --gate v31 --escalate judge_v3 ...`(정책 문서 없이: `--escalate judge_v3_norules`), live는 `scripts/evaluation/live_evaluation.py`의 C6·C7·C7n. 결과는 `results/toolspec/v1/`, `results/rules/v1/`, `results/replay/v3/`, `results/replay/v31/`, `results/live/v3/`, `results/live/v31/`, 수치는 `docs/final_report.md` 6절.
