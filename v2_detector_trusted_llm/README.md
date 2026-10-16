# v2: 판별기 + Trusted LLM (live C5)

툴 응답마다 ML 판별기(PIGuard)를 돌려 "의심" 라벨을 붙이고, 그 라벨을 정책(P10)이 읽어 판정하는 구성이다. ESCALATE는 Trusted LLM v2(claude-sonnet-5-5)가 처리한다. **판별기 오탐이 정책 판정을 덮어써 v3에서 폐기했다.** 결과는 [RESULTS.md](RESULTS.md).

## 흐름

```
태스크 시작   허용 범위 = 사용자 고정 설정(config/user_policy.yaml) ∪ TaskSpec(프롬프트)
툴 응답마다   판별기(PIGuard, 1000자 조각·200자 겹침) → 응답에 라벨(최고 점수, 조각별 점수)
툴 호출마다   ① 읽기 툴 → 통과
              ② 정책: P0 + P1–P9 + P10 → BLOCK / ALLOW / ESCALATE
              ③ ESCALATE → Trusted LLM v2 → ALLOW / BLOCK / HUMAN
              ④ 비가역 툴은 LLM이 ALLOW해도 관련 출처에 판별기 경고가 있으면 HUMAN
```

## 정책

| 정책 | 클래스 | 규칙 → 판정 |
|---|---|---|
| P0 사용자 고정 설정 | `UserFixedPolicy` | `always_block` 툴 → BLOCK, `always_human` 툴(`delete_file`, `update_password`) → 판정 없이 HUMAN |
| P10 판별기 라벨 | `DetectorLabelPolicy` | 판별기가 flag한 응답에서 온 값 → ESCALATE, 지목하지 않은 응답의 의심 조각 안 값 → BLOCK |

Trusted LLM v2의 입력은 사용자 프롬프트, 허용 범위, 툴과 되돌림 가능 여부, ESCALATE 이유, 인자별 값(400자)·출처, 그 출처의 판별 점수와 의심 조각 발췌다. 툴 응답 전문은 넣지 않는다. 답은 첫 줄 ALLOW / BLOCK / HUMAN, 해석 실패는 HUMAN이다. 고정 지시문은 system에 두어 Anthropic prompt caching을 쓴다.

## 폐기 이유

- 판별기가 내용이 아니라 출력 형식(dict·YAML)에 반응했다. 같은 호텔 리뷰가 dict 형식이면 0.9997, 평문이면 0.0이다.
- 그 라벨이 정책의 ALLOW를 덮어써, 판단 주체가 정책에서 판별기로 넘어갔다. 정상 작업 손실의 주원인이었다.

## 코드

이 폴더는 읽기용 사본이다. 실행·테스트는 저장소 루트의 같은 경로 원본으로 한다. 사본은 `python scripts/reporting/sync_version_folders.py`가 원본에서 만들고, `tests/test_version_folders.py`가 원본과 같은지 확인한다.

| 구성 | 파일 |
|---|---|
| 판별기 | `src/agentgate/detector/`, `config/detector.yaml` |
| P0·P10 (발췌) | `src/agentgate/policy/rules_v2_excerpt.py` ← 원본 `src/agentgate/policy/rules.py` |
| 사용자 고정 설정 | `config/user_policy.yaml` |
| Trusted LLM v2 | `src/agentgate/llm/trusted_judge.py`, `prompts/trusted_judge.md`, `prompts/trusted_judge_system.md` |
| 형식 민감도 실험 | `scripts/analysis/detector_format_probe.py` |
| 테스트 | `tests/policy/test_p0.py`, `tests/policy/test_p10.py`, `tests/test_trusted_judge.py` |

판별기 연결(응답 라벨링)과 규칙 ④는 루트 `src/agentgate/runtime.py`의 `GatedRuntime`에 있다. `detector=None`이면 v1과 같게 동작한다.

## 재현 (루트에서)

```bash
python scripts/evaluation/replay_evaluation.py --spec oracle --gate v2 --detector piguard --escalate judge_v2 \
  --mode enforce --out results/replay/v2/c5_judge_piguard
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --attack tool_knowledge \
  --pairs @data/pairs/strat3_seed0.txt --conditions C5 --out results/live/v2/strat3_tk_gpt-oss_120b
```
