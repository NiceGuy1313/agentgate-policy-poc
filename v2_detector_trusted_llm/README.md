# v2: 판별기 + Trusted LLM (live 조건 C5)

v2 구성에 해당하는 파일을 저장소 루트의 같은 상대 경로에서 복사한 참고용 사본이다. 실행과 테스트는 루트의 원본으로 한다(`pytest`는 `tests/`만 수집한다). 원본을 고치면 이 사본은 갱신되지 않는다.

| 구성 | 파일 |
|---|---|
| 판별기(PIGuard, 조각 분할) | `src/agentgate/detector/`, `config/detector.yaml` |
| 사용자 고정 설정(P0) | `config/user_policy.yaml`, `src/agentgate/policy/rules.py`의 `UserFixedPolicy` |
| 판별기 라벨 정책(P10) | `src/agentgate/policy/rules.py`의 `DetectorLabelPolicy` |
| Trusted LLM v2 | `src/agentgate/llm/trusted_judge.py`, `prompts/trusted_judge.md`, `prompts/trusted_judge_system.md` |
| 응답 라벨링과 규칙 ④ | `src/agentgate/runtime.py` |
| Anthropic 호출·prompt caching | `src/agentgate/llm/client.py` |
| 분석 | `scripts/analysis/detector_format_probe.py`(출력 형식 민감도), `scripts/analysis/analyze_live_utility.py`(정상 작업 손실 분해) |
| 테스트 | `tests/policy/test_p0.py`, `tests/policy/test_p10.py`, `tests/test_trusted_judge.py` |

`rules.py`, `runtime.py`, `client.py`는 v3와 공유하는 파일이라 v3 코드도 들어 있다.

재현(루트에서): `python scripts/evaluation/replay_evaluation.py --gate v2 --escalate judge_v2 ...`, live는 `scripts/evaluation/live_evaluation.py`의 C5. 결과는 `results/detector/v1/`, `results/replay/v2/`, `results/live/v2/`, 수치는 `docs/final_report.md` 6절.
