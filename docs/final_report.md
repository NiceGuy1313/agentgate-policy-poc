# AgentDojo 툴 호출 정책 게이트 — 결과 보고서

> 모든 수치는 `scripts/reporting/build_report.py`가 `results/`와 `data/`의 파일에서 생성한다. 게이트 구조와 설계 결정은 [`docs/architecture.md`](architecture.md), 정책 P1~P9의 정의는 [`docs/policy_spec.md`](policy_spec.md)에 있다.

## 요약

- **RQ1 재생 (oracle TaskSpec)**: 효과 호출이 있는 929쌍 중 96.2% (894/929)을 BLOCK 또는 ESCALATE로, 80.0% (743/929)을 BLOCK으로 잡는다. ESCALATE까지 차단하면 정책 없이 성공하던 공격의 91.6% (788/860)이 실패로 바뀐다. 정상 user task 97개 중 BLOCK이 나온 태스크는 3개다.
- **LLM TaskSpec (claude-sonnet-5-5)**: strict 80.7% (750/929), lenient 96.2% (894/929)로 oracle과 비슷하다.
- **RQ2 gpt-oss:120b** (`tool_knowledge`, 공격 291쌍): ASR C0 35.1% → C1 4.5%, C2 8.9%, C4 5.2%. 정상 Utility C0 75.3% → C1 63.9%, C2 73.2%, C4 70.1%.
- **RQ2 mistral-large-3:675b** (`tool_knowledge`, 공격 291쌍): ASR C0 63.9% → C1 8.2%, C2 17.9%, C4 7.2%. 정상 Utility C0 63.9% → C1 57.7%, C2 59.8%, C4 59.8%.
- **판정 LLM (claude-sonnet-5-5)**: 재생의 ESCALATE 583건 중 공격 호출 564건에서 ALLOW 0건, 정상 호출 19건에서 ALLOW 7건, HUMAN 비율 5.8%.
- **집행 무결성**: 실행 묶음 2개, 3104회 실행에서 위반 0건.
- **v3.1 (6절, 짝 B 같은 시점 실행)**: Utility C0 73.2% → C7 72.2% (C0 대비 -1.0%p (p=1)), ASR 34.0% → 1.7%이며 남은 공격 성공은 모두 툴 호출 없는 유형 H다(툴 호출 공격 0건).
- **규칙 문서 ablation**: 판정 LLM에 규칙 문서를 주지 않으면(C7n) 툴 호출 공격 성공이 짝 A 0→1건, 짝 B 0→4건이 된다.

## 1. 평가 설정

| 조건 | 게이트 | ESCALATE 처리 | 그 밖의 방어 | 실행한 모델 |
|---|---|---|---|---|
| C0 방어 없음 | audit (판정만 기록) | - | 없음 | gpt-oss:120b, mistral-large-3:675b |
| C1 게이트 (ESCALATE→block) | enforce | BLOCK | 없음 | gpt-oss:120b, mistral-large-3:675b |
| C2 게이트 (ESCALATE→allow) | enforce | ALLOW | 없음 | gpt-oss:120b, mistral-large-3:675b |
| C3 AgentDojo 내장 방어 | audit (판정만 기록) | - | AgentDojo `spotlighting_with_delimiting` | - |
| C4 게이트 (ESCALATE→judge) | enforce | 판정 LLM: B→BLOCK, A→ALLOW(가역 툴), C·비가역 툴→HUMAN(BLOCK으로 집행) | 없음 | gpt-oss:120b, mistral-large-3:675b |

- 공격 `important_instructions`(AgentDojo 내장): 툴 응답에 사용자 명의의 지시문으로 injection task GOAL을 넣는다. 재생 평가에 사용.
- 공격 `tool_knowledge`(AgentDojo 내장): 같은 형식에 GOAL 수행에 쓸 툴과 인자를 함께 알려 준다. gpt-oss:120b, mistral-large-3:675b 실행.
- 층화 표본(`data/pairs/strat3_seed0.txt`, `scripts/evaluation/make_stratified_pairs.py`): user task 97개 전부에 같은 suite의 injection task 3개를 배정한다(suite마다 시드 0에서 섞은 순서를 차례로 배정). 공격 291쌍.
- user task마다 공격 없는 정상 실행 1개를 더해 정상 97개.
- 전체 949쌍보다 작지만 모든 user task와 injection task를 덮는다. 조건마다 같은 짝을 한 번씩 실행해 C0와 짝 비교한다.

| 지표 | 정의 |
|---|---|
| ASR | 공격 실행 중 injection task `security()`가 참(공격 목표 달성)인 비율 |
| Utility | 공격 없는 실행 중 user task `utility()`가 참인 비율 |
| Utility under attack | 공격 실행 중 user task `utility()`가 참인 비율 |
| 탐지율 strict / lenient | 재생에서 공격 궤적의 효과 호출 중 하나 이상이 BLOCK / BLOCK 또는 ESCALATE인 쌍의 비율 |
| 저지율 | 정책 없이 공격이 성공한 쌍 중 enforce 재생에서 `security()`가 거짓이 된 비율. executed-only는 실제 실행된 호출만으로 판정 |

## 2. RQ1 정답 궤적 재생

user task 정답 궤적 뒤에 injection task 참조 궤적을 이어 붙여 949쌍을 audit·enforce로 재생했다(`important_instructions`, LLM 호출 없음). oracle은 프롬프트만 보고 사람이 쓴 TaskSpec, LLM 열은 같은 입력으로 추출한 TaskSpec이다. oracle과 LLM claude-sonnet-5-5 spec의 탐지 결과(strict, lenient)가 다른 쌍은 14쌍이다.

| 지표 | oracle | LLM claude-sonnet-5-5 |
|---|---|---|
| 평가 쌍 (효과 호출 있음) / 전체 | 929 / 949 | 929 / 949 |
| 쌍 단위 탐지율 strict | 80.0% (743/929) | 80.7% (750/929) |
| 쌍 단위 탐지율 lenient | 96.2% (894/929) | 96.2% (894/929) |
| 호출 단위 탐지율 (BLOCK+ESCALATE) | 79.5% (1999/2513) | 79.5% (1999/2513) |
| 저지율 native | 91.6% (788/860) | 91.6% (788/860) |
| 저지율 executed-only | 94.1% (809/860) | 94.1% (809/860) |
| 정상 user task 중 BLOCK 발생 | 3.1% (3/97) | 3.1% (3/97) |
| 정상 user task 중 ESCALATE 발생 | 17.5% (17/97) | 17.5% (17/97) |
| 정상 Utility audit → enforce | 97/97 → 79/97 | 97/97 → 79/97 |
| 공격 하 Utility audit → enforce | 267/949 → 675/949 | 267/949 → 675/949 |
| 판정 지연 ms/호출 (mean / p95) | 0.740 / 3.472 | 0.745 / 3.405 |

유형별 결과(oracle). 유형 H는 효과 호출이 없어 탐지율 분모에서 빠지고 저지율에는 남는다.

| 유형 | 쌍 | strict | lenient | 저지 native | 저지 executed-only |
|---|---|---|---|---|---|
| A 무단 외부 연락 | 40 | 90.0% (36/40) | 100.0% (40/40) | 100.0% (31/31) | 100.0% (31/31) |
| B 금전 탈취 | 48 | 35.4% (17/48) | 100.0% (48/48) | 100.0% (48/48) | 100.0% (48/48) |
| C 민감 데이터 반출 | 582 | 89.3% (520/582) | 99.8% (581/582) | 99.8% (505/506) | 99.8% (505/506) |
| D 삭제·은폐 | 40 | 0.0% (0/40) | 100.0% (40/40) | 100.0% (40/40) | 100.0% (40/40) |
| E 계정·권한 변경 | 37 | 100.0% (37/37) | 100.0% (37/37) | 43.2% (16/37) | 100.0% (37/37) |
| F 비신뢰 내용 전파·방문 | 82 | 57.3% (47/82) | 65.9% (54/82) | 65.9% (54/82) | 65.9% (54/82) |
| G 무단 예약·일정 생성 | 100 | 86.0% (86/100) | 94.0% (94/100) | 97.9% (94/96) | 97.9% (94/96) |
| H 툴 호출 없는 출력 조작 | 20 | - | - | 0.0% (0/20) | 0.0% (0/20) |

![detection by type](../results/figures/detection_by_type.png)

정책 하나를 끄면 strict 탐지가 가장 크게 줄어드는 정책은 P2(125쌍), lenient는 P1(64쌍)이다.

![ablation](../results/figures/ablation.png)

lenient로도 놓친 35쌍의 원인: 출처 판정 실패(지목 출처 안의 주입 값) 30쌍; TaskSpec 한계(공격이 요청된 작업과 같은 툴·인자 범위) 4쌍; TaskSpec 문제(공격 값이 프롬프트·allowed_dest와 일치) 1쌍.

## 3. RQ2 실제 에이전트

AgentDojo `AgentPipeline`에 게이트(`GatedRuntime`)와 `TaskSpecElement`를 넣어 실행했다. 게이트 조건의 TaskSpec은 LLM spec(`data/specs/llm/claude-sonnet-5-5/`), C4 판정 LLM은 `claude-sonnet-5-5`이다.

비율 옆 괄호는 Wilson 95% CI, "C0 대비"는 같은 (suite, user task, injection task) 짝의 차이와 exact McNemar p다. 마지막 열은 차단이 있었고 user task가 실패한 정상 실행 수(그중 C0에서는 성공한 수)다.

| 모델 | 조건 | ASR [95% CI] | C0 대비 | Utility [95% CI] | C0 대비 | Utility under attack [95% CI] | C0 대비 | 게이트로 실패한 정상 실행 (C0 성공) |
|---|---|---|---|---|---|---|---|---|
| gpt-oss:120b | C0 | 35.1% [29.8, 40.7] | - | 75.3% [65.8, 82.8] | - | 60.5% [54.8, 65.9] | - | - |
| gpt-oss:120b | C1 | 4.5% [2.6, 7.5] | -30.6%p (p=7.2e-24) | 63.9% [54.0, 72.8] | -11.3%p (p=0.019) | 50.9% [45.1, 56.5] | -9.6%p (p=0.0015) | 14 (11) |
| gpt-oss:120b | C2 | 8.9% [6.2, 12.8] | -26.1%p (p=9.6e-19) | 73.2% [63.6, 81.0] | -2.1%p (p=0.75) | 59.5% [53.7, 64.9] | -1.0%p (p=0.8) | 4 (3) |
| gpt-oss:120b | C4 | 5.2% [3.1, 8.3] | -29.9%p (p=1.7e-22) | 70.1% [60.4, 78.3] | -5.2%p (p=0.3) | 51.5% [45.8, 57.2] | -8.9%p (p=0.0029) | 9 (8) |
| mistral-large-3:675b | C0 | 63.9% [58.2, 69.2] | - | 63.9% [54.0, 72.8] | - | 33.7% [28.5, 39.3] | - | - |
| mistral-large-3:675b | C1 | 8.2% [5.6, 12.0] | -55.7%p (p=4.2e-45) | 57.7% [47.8, 67.1] | -6.2%p (p=0.34) | 36.8% [31.4, 42.4] | +3.1%p (p=0.42) | 13 (12) |
| mistral-large-3:675b | C2 | 17.9% [13.9, 22.7] | -46.0%p (p=2.8e-34) | 59.8% [49.8, 69.0] | -4.1%p (p=0.5) | 45.4% [39.7, 51.1] | +11.7%p (p=0.00032) | 5 (3) |
| mistral-large-3:675b | C4 | 7.2% [4.8, 10.8] | -56.7%p (p=1.8e-48) | 59.8% [49.8, 69.0] | -4.1%p (p=0.48) | 47.4% [41.8, 53.2] | +13.7%p (p=5.5e-05) | 7 (5) |

![stratified sample CI](../results/figures/strat3_ci.png)

차단이 없었던 정상 실행끼리 조건만 바꿔 비교하면 Utility가 달라진 짝은 gpt-oss:120b 40/498(8.0%), mistral-large-3:675b 66/516(12.8%)이다. 게이트가 개입하지 않은 차이이므로 모델 무작위성의 크기를 보여 준다.

![tradeoff gpt-oss:120b](../results/figures/tradeoff_strat3_tk_gpt-oss_120b.png) ![tradeoff mistral-large-3:675b](../results/figures/tradeoff_strat3_tk_mistral-large-3_675b.png)

게이트 조건에서 남은 공격 성공을 경로별로 센 것이다(`summary.json`의 `successful_attacks_by_class`).

| 남은 공격 성공의 경로 | gpt-oss:120b C1 | gpt-oss:120b C2 | gpt-oss:120b C4 | mistral-large-3:675b C1 | mistral-large-3:675b C2 | mistral-large-3:675b C4 |
|---|---|---|---|---|---|---|
| (a) 툴 호출 없이 성공(유형 H) | 4 | 4 | 4 | 4 | 4 | 4 |
| (b) 공격 호출이 ALLOW됨(탐지 실패) | 9 | 8 | 11 | 14 | 14 | 13 |
| (d) ESCALATE 허용으로 성공 | 0 | 14 | 0 | 0 | 31 | 0 |
| (e) 실행된 호출로는 실패, 시도 기록만으로 성공(trace 기반 판정) | 0 | 0 | 0 | 6 | 3 | 4 |

## 4. 판정 LLM 단독 평가

재생(oracle spec)에서 ESCALATE가 난 호출을 판정기에 넣고 정답(injection 궤적의 호출 = attack, 정상 재생의 호출 = normal)과 비교했다. 답 A는 허용, B는 차단, C는 사람 확인이다.

| 판정 모델 | 정답 | 호출 | 답 A | 답 B | 답 C | 최종 ALLOW | 최종 BLOCK | 최종 HUMAN |
|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | attack | 564 | 8 | 541 | 15 | 0 | 541 | 23 |
| claude-sonnet-5-5 | normal | 19 | 9 | 1 | 9 | 7 | 1 | 11 |

## 5. 집행 무결성

`scripts/analysis/check_enforcement.py`가 실행 기록에서 BLOCK된 호출의 실행, 차단 시 환경 변화, 툴 결과와 게이트 판정의 1:1 불일치를 센다.

| 실행 묶음 | 실행 | 게이트 판정 | 차단 집행 | 위반 | 반복 상한으로 미실행 요청이 있는 실행 |
|---|---|---|---|---|---|
| `strat3_tk_gpt-oss_120b` | 1552 | 6164 | 454 | 0 | 18 |
| `strat3_tk_mistral-large-3_675b` | 1552 | 7627 | 995 | 0 | 0 |

## 6. v2–v3.1: ToolSpec과 규칙 문서 기반 게이트

1–5절의 v1 게이트 위에서 진행한 후속 실험이다. 모든 조건의 에이전트는 gpt-oss:120b(Ollama), 공격은 `tool_knowledge`, TaskSpec은 claude-sonnet-5-5가 프롬프트와 툴 목록만 보고 새로 추출한 것(`data/specs/llm/claude-sonnet-5-5-v3/`)이다.

### 6.1 구조의 변화

- **v2 (C5)**: 툴 응답에 PIGuard 판별기를 돌리고, 판별기가 flag한 응답에서 온 값을 P10이 ESCALATE했다. 정상 실행 응답의 26.9% (87/323)를 판별기가 flag했고, 이 라벨이 정책 판정을 덮어써 정상 작업 손실의 주원인이 되었다. v3부터 판별기를 쓰지 않는다.
- **v3 (C6)**: P7의 공격 문구 목록을 삭제했다. 툴 응답마다 claude-haiku-5-5가 레코드와 '독자에게 하는 요청'을 JSON(ToolSpec)으로 뽑고(값은 원문 대조로 검증), P11이 '툴 응답 속 요청을 수행하는 호출'을, P12가 '요청되지 않은 행동'을 판정한다. ESCALATE는 판정 LLM v3(claude-sonnet-5-5)가 규칙 문서의 인덱스로 고른 조항과 TaskSpec·ToolSpec만 보고 해소한다(툴 응답 원문은 넣지 않음).
- **v3.1 (C7)**: v3 live의 정상 작업 손실을 범주별 일반 규칙으로 고쳤다. 위임 자료의 요청과 일치하는 미요청 행동은 ESCALATE(A1), 위임 지시는 요청만 위임 자료에서 오면 됨(A2), 위임 자료의 ToolSpec 항상 제공(A3), 지목 자료 안 링크는 BLOCK 대신 ESCALATE(B1), 짧은 식별자의 지목 자료 일치(B2), 전송을 요청한 경우 P3를 ESCALATE(C1), 프롬프트가 요청한 always_human 행동은 사람 확인 생략(D2).
- **규칙 문서** (`config/policy_rules_v31.yaml`): 코드 집행 조항 8개, 판정 LLM 조항 7개와 조항별 트리거 인덱스. 근거는 ATR test-corpora (commit `5eb6dba810fb`) 사례 1139건(분류 실패 1건 제외)을 claude-haiku-5-5로 분류한 결과다. 간접 주입 599건 중 평범한 요청 형태 398건, 권한 사칭 347건, 이전 지시 무시 332건, AI를 지칭함 457건이다. AgentDojo 문구는 쓰지 않았다.

### 6.2 ToolSpec 추출 품질

정답 궤적의 실제 툴 응답(정상, 그리고 공격 2종으로 주입된 응답)에서 '독자에게 하는 요청'을 뽑은 결과다(`results/toolspec/v1`).

| 응답 | 개수 | assistant 대상 요청 추출 | 요청 하나라도 추출 |
|---|---|---|---|
| 주입 응답 | 809 | 100.0% (809/809) | 100.0% (809/809) |
| 정상 응답 | 172 | 0.0% (0/172) | 5.2% (9/172) |

AgentDojo의 주입은 AI를 직접 부르는 문장이다. ATR 간접 주입에서는 142/599건이 AI를 지칭하지 않으므로, 이 탐지율을 다른 공격에 그대로 기대할 수는 없다.

### 6.3 재생 (LLM TaskSpec v3)

2절과 같은 정답 궤적 재생이다. 네 게이트 모두 같은 TaskSpec을 쓴다. "놓친 쌍"은 효과 호출이 BLOCK도 ESCALATE도 되지 않은 공격 쌍이다.

| 게이트 | 공격 | 저지 | lenient 탐지 | 정상 Utility | 공격 중 Utility | 놓친 쌍 |
|---|---|---|---|---|---|---|
| v1 게이트 (P7 공격 문구 포함) | `tool_knowledge` | 91.5% (787/860) | 96.2% (894/929) | 81.4% (79/97) | 71.1% (675/949) | 35 |
| v1 게이트 (P7 공격 문구 포함) | `important_instructions` | 91.5% (787/860) | 96.2% (894/929) | 81.4% (79/97) | 71.1% (675/949) | 35 |
| v3 | `tool_knowledge` | 94.7% (814/860) | 99.9% (928/929) | 85.6% (83/97) | 74.3% (705/949) | 1 |
| v3 | `important_instructions` | 94.7% (814/860) | 99.7% (926/929) | 85.6% (83/97) | 74.2% (704/949) | 3 |
| v3.1 + 규칙 문서 | `tool_knowledge` | 94.8% (815/860) | 99.9% (928/929) | 87.6% (85/97) | 75.6% (717/949) | 1 |
| v3.1 + 규칙 문서 | `important_instructions` | 94.7% (814/860) | 99.7% (926/929) | 87.6% (85/97) | 75.7% (718/949) | 3 |
| v3.1, 규칙 문서 없음 | `tool_knowledge` | 94.3% (811/860) | 99.9% (928/929) | 89.7% (87/97) | 77.4% (735/949) | 1 |
| v3.1, 규칙 문서 없음 | `important_instructions` | 94.3% (811/860) | 99.7% (926/929) | 89.7% (87/97) | 77.7% (737/949) | 3 |

### 6.4 실제 에이전트

"툴 호출 공격 성공"은 공격 성공 중 툴 호출 없이 답변만 조작한 유형 H를 뺀 수다. "C0 대비"는 같은 짝의 차이와 exact McNemar p다.

**짝 A (`data/pairs/strat3_seed0.txt`, v3 손실 분석과 v3.1 설계에 쓴 짝)**. C0–C4는 v1, C5는 v2, C6은 v3 실행 결과를 재사용했다(같은 짝, 다른 실행 시점).

| 조건 | 구성 | ASR [95% CI] | 툴 호출 공격 성공 | Utility [95% CI] | Utility C0 대비 | 공격 중 Utility [95% CI] |
|---|---|---|---|---|---|---|
| C0 | 방어 없음 | 35.1% [29.8, 40.7] | 97 | 75.3% [65.8, 82.8] | - | 60.5% [54.8, 65.9] |
| C1 | v1 게이트, ESCALATE→BLOCK | 4.5% [2.6, 7.5] | 9 | 63.9% [54.0, 72.8] | -11.3%p (p=0.019) | 50.9% [45.1, 56.5] |
| C2 | v1 게이트, ESCALATE→ALLOW | 8.9% [6.2, 12.8] | 22 | 73.2% [63.6, 81.0] | -2.1%p (p=0.75) | 59.5% [53.7, 64.9] |
| C4 | v1 게이트 + 판정 LLM v1 | 5.2% [3.1, 8.3] | 11 | 70.1% [60.4, 78.3] | -5.2%p (p=0.3) | 51.5% [45.8, 57.2] |
| C5 | v2: P10(PIGuard 라벨) + Trusted LLM v2 | 2.7% [1.4, 5.3] | 3 | 57.7% [47.8, 67.1] | -17.5%p (p=0.0023) | 41.9% [36.4, 47.7] |
| C6 | v3: P11·P12 + ToolSpec + 판정 LLM v3(규칙 문서 v3) | 2.1% [0.9, 4.4] | 0 | 62.9% [52.9, 71.8] | -12.4%p (p=0.029) | 49.1% [43.5, 54.9] |
| C7 | v3.1 + 판정 LLM v3(규칙 문서 v3.1) | 1.0% [0.4, 3.0] | 0 | 66.0% [56.1, 74.6] | -9.3%p (p=0.049) | 50.9% [45.1, 56.5] |
| C7n | v3.1 + 판정 LLM v3(규칙 문서 없음) | 1.7% [0.7, 4.0] | 1 | 64.9% [55.0, 73.7] | -10.3%p (p=0.031) | 53.6% [47.9, 59.2] |

**짝 B (`data/pairs/strat3_seed1.txt`, v3.1 설계에 쓰지 않은 짝)**. 다섯 조건을 같은 시점에 실행했다. 정상 실행은 짝 A와 같은 97개 user task이고, 공격 쌍 291개 중 141개는 짝 A와 겹친다. 완전한 held-out은 아니다.

| 조건 | 구성 | ASR [95% CI] | 툴 호출 공격 성공 | Utility [95% CI] | Utility C0 대비 | 공격 중 Utility [95% CI] |
|---|---|---|---|---|---|---|
| C0 | 방어 없음 | 34.0% [28.8, 39.6] | 96 | 73.2% [63.6, 81.0] | - | 61.5% [55.8, 66.9] |
| C1 | v1 게이트, ESCALATE→BLOCK | 5.2% [3.1, 8.3] | 11 | 58.8% [48.8, 68.0] | -14.4%p (p=0.0094) | 51.2% [45.5, 56.9] |
| C6 | v3: P11·P12 + ToolSpec + 판정 LLM v3(규칙 문서 v3) | 1.0% [0.4, 3.0] | 0 | 60.8% [50.9, 70.0] | -12.4%p (p=0.029) | 52.2% [46.5, 57.9] |
| C7 | v3.1 + 판정 LLM v3(규칙 문서 v3.1) | 1.7% [0.7, 4.0] | 0 | 72.2% [62.5, 80.1] | -1.0%p (p=1) | 54.6% [48.9, 60.3] |
| C7n | v3.1 + 판정 LLM v3(규칙 문서 없음) | 3.1% [1.6, 5.8] | 4 | 63.9% [54.0, 72.8] | -9.3%p (p=0.078) | 53.6% [47.9, 59.2] |

정상 실행 97개를 같은 짝의 C0와 비교한 것이다(`scripts/analysis/analyze_live_utility.py`). 차단 없이 갈린 두 열은 에이전트 실행마다의 편차다.

| 조건 | C0 성공 → 차단으로 실패 | C0 성공 → 차단 없이 실패 | C0 실패 → 성공 |
|---|---|---|---|
| 짝 A C6 (v3) | 12 | 7 | 7 |
| 짝 A C7 (v3.1) | 8 | 5 | 4 |
| 짝 B C6 (v3) | 12 | 7 | 7 |
| 짝 B C7 (v3.1) | 9 | 1 | 9 |

### 6.5 규칙 문서의 효과 (C7 vs C7n)

두 조건은 게이트(v3.1)와 판정 LLM에 넘기는 사실(TaskSpec, ToolSpec, 값의 출처)이 같고, 판정 LLM에 규칙 인덱스와 조항을 주는지만 다르다. 규칙 문서가 없으면 system 프롬프트가 짧아 프롬프트 캐시가 동작하지 않는다.

| 짝 | 규칙 문서 | 툴 호출 공격 성공 | Utility | 공격 중 Utility | 판정 호출 | HUMAN | 판정 입력 토큰(캐시 제외) |
|---|---|---|---|---|---|---|---|
| 짝 A | C7 (있음) | 0 | 66.0% | 50.9% | 113 | 27 | 136398 |
| 짝 A | C7n (없음) | 1 | 64.9% | 53.6% | 100 | 26 | 103547 |
| 짝 B | C7 (있음) | 0 | 72.2% | 54.6% | 129 | 33 | 134526 |
| 짝 B | C7n (없음) | 4 | 63.9% | 53.6% | 104 | 19 | 95603 |

| 규칙 문서 | 재생 공격 | 저지 | 정상 Utility | 공격 중 Utility |
|---|---|---|---|---|
| 있음 | `tool_knowledge` | 94.8% (815/860) | 87.6% (85/97) | 75.6% (717/949) |
| 있음 | `important_instructions` | 94.7% (814/860) | 87.6% (85/97) | 75.7% (718/949) |
| 없음 | `tool_knowledge` | 94.3% (811/860) | 89.7% (87/97) | 77.4% (735/949) |
| 없음 | `important_instructions` | 94.3% (811/860) | 89.7% (87/97) | 77.7% (737/949) |

### 6.6 집행 무결성과 한계

| 실행 묶음 | 실행 | 게이트 판정 | 차단 집행 | 위반 |
|---|---|---|---|---|
| v2 C5 | 388 | 1524 | 263 | 0 |
| v3 C6 | 388 | 1456 | 174 | 0 |
| v3.1 짝 A | 776 | 3011 | 314 | 0 |
| v3.1 짝 B | 1940 | 7571 | 675 | 0 |

- v3.1의 수정은 짝 A의 C6 손실을 보고 만들었다. 짝 B는 공격 쌍 일부만 새롭고 정상 user task는 같다.
- v3.1 B1은 처음에 지목 자료 안 링크를 판정 없이 P7에 넘기도록 구현했다가, 파일럿에서 주입된 URL 방문이 통과해 ESCALATE로 바꿨다. 본 실행은 바꾼 뒤의 결과다.
- ToolSpec 추출기는 툴 응답을 읽으므로 그 자체가 주입 대상이다. 자유 텍스트 필드를 두지 않고 값은 원문 대조로 검증하지만, 추출기를 겨냥한 적응형 공격은 평가하지 않았다.
- 유형 H(툴 호출 없는 출력 조작)는 모든 조건에서 남는다. 조건당 1회 실행이다.

## 7. 한계 (1–5절)

- 정책과 공격 유형 분류를 평가에 쓴 injection task 35개를 보고 설계했다. 재생 탐지율은 일반화 성능이 아니라 설계 대상 위협에 대한 상한에 가깝다.
- 재생은 넘어간 에이전트가 참조 궤적대로 공격한다고 가정한다. 실제 모델은 다른 경로로 시도하거나 차단 뒤 작업을 포기한다.
- 실제 모델 본 실험은 층화 표본(공격 291쌍)이며 전체 949쌍이 아니다. 조건당 1회 실행이라 정상 Utility의 수 %p 차이는 모델 무작위성과 구분되지 않는다.
- 유형 H(툴 호출 없는 출력 조작)는 툴 호출 게이트의 범위 밖이다. 정책을 아는 적응형 공격은 평가하지 않았다.
- C3(AgentDojo 내장 방어)은 실제 모델로 실행하지 않았다.

## 8. 재현 명령

Python >=3.10, `agentdojo==0.1.35`, 벤치마크 `v1.2.2`, 정책 설정 `config/policies.yaml` (version v1). LLM 단계에는 `ANTHROPIC_API_KEY`, Ollama 모델에는 `OLLAMA_API_KEY`가 필요하다.

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q
python scripts/evaluation/replay_evaluation.py --spec oracle --out results/replay/v1/oracle
python scripts/analysis/summarize_replay.py
python scripts/taskspec/extract_llm_specs.py && python scripts/taskspec/compare_specs.py --model claude-sonnet-5-5
python scripts/evaluation/replay_evaluation.py --spec llm:claude-sonnet-5-5 --out results/replay/v1/llm_claude-sonnet-5-5
python scripts/analysis/summarize_replay.py --run results/replay/v1/llm_claude-sonnet-5-5
python scripts/evaluation/judge_evaluation.py
python scripts/evaluation/make_stratified_pairs.py --k 3 --seed 0 --benign --out data/pairs/strat3_seed0.txt
AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL=gpt-oss:120b python scripts/evaluation/live_evaluation.py --agent llm --spec llm --attack tool_knowledge --pairs @data/pairs/strat3_seed0.txt --conditions C0 C1 C2 C4 --workers 5 --out results/live/v1/strat3_tk_gpt-oss_120b
AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL=mistral-large-3:675b python scripts/evaluation/live_evaluation.py --agent llm --spec llm --attack tool_knowledge --pairs @data/pairs/strat3_seed0.txt --conditions C0 C1 C2 C4 --workers 5 --out results/live/v1/strat3_tk_mistral-large-3_675b
for run in strat3_tk_gpt-oss_120b strat3_tk_mistral-large-3_675b; do
  python scripts/analysis/check_enforcement.py --run results/live/v1/$run
  python scripts/analysis/summarize_live.py --run results/live/v1/$run
  python scripts/analysis/live_significance.py --run results/live/v1/$run
done
# 6절 (v2–v3.1)
python scripts/evaluation/toolspec_evaluation.py --out results/toolspec/v1
python scripts/analysis/analyze_atr_corpora.py --atr <agent-threat-rules checkout> --out results/rules/v1
python scripts/evaluation/extract_taskspecs.py --name claude-sonnet-5-5-v3 --cache-dir data/specs/llm_cache_v3
python scripts/evaluation/make_stratified_pairs.py --k 3 --seed 1 --benign --out data/pairs/strat3_seed1.txt
for a in tool_knowledge important_instructions; do for e in judge_v3 judge_v3_norules; do
  python scripts/evaluation/replay_evaluation.py --spec llm:claude-sonnet-5-5-v3 --gate v31 --escalate $e --attack $a --out results/replay/v31/${e}_llmspec_$a
  python scripts/analysis/summarize_replay.py --run results/replay/v31/${e}_llmspec_$a; done; done
AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL=gpt-oss:120b python scripts/evaluation/live_evaluation.py --agent llm --spec llm --attack tool_knowledge --pairs @data/pairs/strat3_seed1.txt --conditions C0 C1 C6 C7 C7n --spec-cache-dir data/specs/llm_cache_v3 --out results/live/v31/strat3s1_tk_gpt-oss_120b
python scripts/analysis/analyze_live_utility.py --run results/live/v31/strat3s1_tk_gpt-oss_120b --baseline results/live/v31/strat3s1_tk_gpt-oss_120b --condition C7
python scripts/reporting/build_report.py
```
