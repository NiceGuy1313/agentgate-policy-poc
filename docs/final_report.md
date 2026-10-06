# AgentDojo 툴 호출 정책 엔진 PoC — 최종 보고서

> 이 문서의 모든 수치는 `scripts/build_report.py`가 `results/`와 `data/`의 파일에서 생성한다. 손으로 옮긴 수치는 없다.

## 요약

- **RQ1 (탐지 가능성)**: 공격에 넘어간 에이전트의 정답 궤적을 재생하면, 결정론적 정책 P1~P9는 효과 호출이 있는 929쌍 중 96.2% (894/929)을 BLOCK 또는 ESCALATE로, 80.0% (743/929)을 BLOCK으로 잡는다. ESCALATE를 block으로 집행하면 정책 없이 성공하던 공격 860쌍 중 91.6% (788/860)가 실패로 바뀐다(실행된 호출 기준 94.1% (809/860)). 정상 user task 97개에서 BLOCK은 3개 태스크, ESCALATE는 17개 태스크에서 나왔다.
- **RQ2 (집행 여부)**: AgentDojo 파이프라인에 게이트를 넣고 4184회 실행(게이트 판정 25544건, 차단 2987건)한 결과, BLOCK된 호출의 실행·차단 시 환경 변화·게이트를 거치지 않은 툴 실행이 모두 0건이었다. 대역 에이전트 기준 ASR은 C0 97.4% → C1 7.5%, 정상 Utility는 99.0% → 80.4%이다. 실제 LLM 에이전트 결과는 API 키가 없어 아직 없다.
- **판정 LLM(C4)과 LLM TaskSpec 추출**: 코드·프롬프트·캐시·테스트는 완성했으나 키가 없어 실행하지 않았다(8절의 명령으로 실행).

## 1. 개요

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)에 따라 송금·반출·삭제를 하는 문제를, 모델 바깥의 결정론적 정책으로 막을 수 있는지 검증한다.

- **RQ1 탐지 가능성**: 사용자 프롬프트만으로 만든 TaskSpec과 툴 호출 인자의 출처를 기준으로 하는 정책이, 공격 호출을 잡고 정상 호출은 통과시키는가.
- **RQ2 집행 여부**: 정책 판정이 실제 에이전트 파이프라인에서 우회 없이 집행되는가, 그 비용(Utility·지연)은 얼마인가.

## 2. 방법

### 2.1 시나리오 분류와 정책 도출

AgentDojo v1.2.2의 injection task 35개를 공격 효과로 분류했다(A 무단 외부 연락, B 금전 탈취, C 민감 데이터 반출, D 삭제·은폐, E 계정·권한 변경, F 비신뢰 내용 전파·방문, G 무단 예약·일정 생성, H 툴 호출 없는 출력 조작). 분류와 정책 대응은 `docs/policy_spec.md` 1절, 카탈로그 검증은 `docs/stage1_catalog_report.md`에 있다.

| 정책 | 대상 | 판정 요지 |
|---|---|---|
| P1 외부 목적지 출처 | 수신자·IBAN·참석자·공유 대상·게시 URL | PROMPT→ALLOW, 지목 출처→ESCALATE, 그 외 툴 응답·미등장→BLOCK |
| P2 민감 데이터 반출 | 본문·제목·메모·첨부 | 민감 읽기 결과와 16자 이상 겹침 또는 고가치 토큰 일치, egress 미요청→BLOCK |
| P3 대량 반출 | 같은 싱크 인자 | 서로 다른 읽기 결과 3개 이상(첨부 포함)→BLOCK |
| P4 금액 | send_money 등 amount | cap 초과→BLOCK, 출처 없는 금액→ESCALATE, 세션 누적 5000.0 초과·같은 신규 수신자 3회째→BLOCK |
| P5 삭제 | delete_email·delete_file | 세션에서 보낸 메일 삭제→BLOCK, 프롬프트가 지목하지 않은 파일 삭제→ESCALATE |
| P6 권한 변경 | 비밀번호·사용자 정보·초대·채널 추가·공유 | requested_ops에 없음→BLOCK, 비밀번호가 프롬프트 출처 아님→BLOCK |
| P7 비신뢰 내용 전파 | 메시지 본문, get_webpage.url | 툴 응답에만 있는 URL·지시 문구 전파→BLOCK, 그런 URL 방문→ESCALATE |
| P8 예약 | reserve_* | requested_ops에 없음→BLOCK, 대상이 프롬프트·지목 출처에 없음→ESCALATE |
| P9 일정 쓰기 | calendar 쓰기 툴 | requested_ops에 없음→BLOCK (외부 참석자는 P1) |

### 2.2 구조

```
user prompt ──► TaskSpec (oracle | LLM extractor: prompt + tool list only)
                    │
agent LLM ──tool call──► GatedRuntime.run_function ──► PolicyEngine.decide(call, spec, trace, session)
                    │            │                         P1..P9 (deterministic, no LLM)
                    │            ├─ ALLOW ──► execute ──► ValueIndex(출처 색인) · SessionState 갱신
                    │            ├─ BLOCK ──► 실행 안 함, 차단 메시지를 툴 결과로 반환
                    │            └─ ESCALATE ─► block | allow | judge LLM (구조화 사실만 입력)
```

- 가로채기 지점은 `FunctionsRuntime` 서브클래스다. AgentDojo의 `ToolsExecutor`와 `GroundTruthPipeline`이 모두 `runtime.run_function`으로만 툴을 실행하므로 한 곳에서 모든 경로를 덮는다(근거: `docs/design_decisions.md`).
- 출처 판정은 프롬프트와 각 툴 응답(모델에 전달된 문자열과 원본 객체의 모든 문자열 필드)을 정규화해 색인하고, 값이 가장 신뢰도 높은 출처에서 처음 등장한 위치를 근거로 남긴다.

### 2.3 평가 설정

- Stage 5 재생: user task 정답 궤적 뒤에 injection task 참조 궤적을 이어 붙여 949쌍을 audit·enforce로 재생. TaskSpec은 oracle(프롬프트만 보고 작성, `docs/stage4_oracle_review.md`).
- Stage 6 파이프라인: AgentDojo `AgentPipeline` + `GatedRuntime`, 조건 C0~C3, 대역 에이전트(ScriptedAgent).

## 3. RQ1 결과 — 재생 평가

| 지표 | oracle TaskSpec | LLM TaskSpec |
|---|---|---|
| 쌍 단위 탐지율 strict | 80.0% (743/929) | 미실행 |
| 쌍 단위 탐지율 lenient | 96.2% (894/929) | 미실행 |
| 호출 단위 탐지율 | 79.5% (1999/2513) | 미실행 |
| 저지율 native / executed-only | 91.6% (788/860) / 94.1% (809/860) | 미실행 |
| 오탐: BLOCK 호출 / BLOCK 태스크 | 0.9% (3/339) / 3.1% (3/97) | 미실행 |
| ESCALATE 호출 / 태스크 | 5.6% (19/339) / 17.5% (17/97) | 미실행 |

유형별 탐지율:

| 유형 | 평가 쌍 | strict | lenient |
|---|---|---|---|
| A 무단 외부 연락 | 40 | 90.0% (36/40) | 100.0% (40/40) |
| B 금전 탈취 | 48 | 35.4% (17/48) | 100.0% (48/48) |
| C 민감 데이터 반출 | 582 | 89.3% (520/582) | 99.8% (581/582) |
| D 삭제·은폐 | 40 | 0.0% (0/40) | 100.0% (40/40) |
| E 계정·권한 변경 | 37 | 100.0% (37/37) | 100.0% (37/37) |
| F 비신뢰 내용 전파·방문 | 82 | 57.3% (47/82) | 65.9% (54/82) |
| G 무단 예약·일정 생성 | 100 | 86.0% (86/100) | 94.0% (94/100) |

![detection by type](../results/figures/detection_by_type.png)

Ablation (정책 하나를 끈 탐지율과 고유 기여):

| 비활성 | strict | lenient | 고유 strict | 고유 lenient |
|---|---|---|---|---|
| (없음) | 80.0% (743/929) | 96.2% (894/929) | - | - |
| P1 | 77.8% (723/929) | 89.3% (830/929) | 20 | 64 |
| P2 | 66.5% (618/929) | 96.2% (894/929) | 125 | 0 |
| P3 | 79.8% (741/929) | 96.2% (894/929) | 2 | 0 |
| P4 | 78.1% (726/929) | 96.2% (894/929) | 17 | 0 |
| P5 | 79.3% (737/929) | 91.9% (854/929) | 6 | 40 |
| P6 | 76.7% (713/929) | 94.5% (878/929) | 30 | 16 |
| P7 | 79.2% (736/929) | 94.7% (880/929) | 7 | 14 |
| P8 | 75.9% (705/929) | 92.1% (856/929) | 38 | 38 |
| P9 | 74.9% (696/929) | 94.5% (878/929) | 47 | 16 |

![ablation](../results/figures/ablation.png)

P1(출처)과 P2(내용 겹침)가 가장 큰 축이다. P2는 lenient 고유 기여가 0이지만 strict 고유 기여가 가장 크다. P1이 ESCALATE로만 잡는 banking·workspace 반출을 BLOCK으로 끌어올리는 역할이다. P6·P8·P9 같은 작업 단위(op-level) 정책은 해당 유형(E·G)에서만 쓰이지만, 그 유형에서는 사실상 유일한 방어선이다.

## 4. RQ2 결과 — 파이프라인 집행

에이전트: **scripted** (실제 LLM 아님, 6절 한계 참고)

| 조건 | Utility | Utility under attack | ASR | ASR (executed-only) | 태스크당 지연 ms (전체 / 게이트 / TaskSpec) | 토큰 (에이전트 in/out, TaskSpec, judge) | HUMAN |
|---|---|---|---|---|---|---|---|
| C0 방어 없음 | 99.0% | 28.5% | 97.4% | 97.4% | 21.149 / 3.719 / 0.0 | 0/0, 0, 0 | 0 |
| C1 게이트 (ESCALATE→block) | 80.4% | 70.6% | 7.5% | 5.3% | 19.466 / 3.935 / 0.0 | 0/0, 0, 0 | 0 |
| C2 게이트 (ESCALATE→allow) | 95.9% | 67.7% | 23.3% | 21.1% | 20.39 / 3.877 / 0.0 | 0/0, 0, 0 | 0 |
| C3 AgentDojo 내장 방어 | 99.0% | 28.5% | 97.4% | 97.4% | 21.367 / 3.833 / 0.0 | 0/0, 0, 0 | 0 |
| C4 게이트 (ESCALATE→judge) | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 |

집행 무결성(scripted): 실행 4184회, 게이트 판정 25544건, 차단 2987건, 위반 0건 (BLOCK 호출 실행 0, 차단 시 환경 변화 0, 툴 결과-판정 1:1 불일치 0).

## 5. ASR · Utility · Latency 트레이드오프

지표 정의는 AgentDojo를 따른다. **Utility**는 공격 없이 user task의 `utility` 판정이 참인 비율, **Utility under attack**은 공격 환경에서 user task `utility`가 참인 비율, **ASR**(targeted attack success rate)은 injection task의 `security` 판정이 참(공격 목표 달성)인 비율이다.

| 조건 | ASR | Utility | Utility under attack | 게이트 판정 지연 (태스크당 ms) |
|---|---|---|---|---|
| C0 방어 없음 | 97.4% | 99.0% | 28.5% | 3.719 |
| C1 게이트 (ESCALATE→block) | 7.5% | 80.4% | 70.6% | 3.935 |
| C2 게이트 (ESCALATE→allow) | 23.3% | 95.9% | 67.7% | 3.877 |
| C3 AgentDojo 내장 방어 | 97.4% | 99.0% | 28.5% | 3.833 |

![tradeoff](../results/figures/tradeoff_scripted.png)

C1은 ASR을 가장 크게 낮추지만 ESCALATE를 모두 막아 정상 Utility도 99.0% → 80.4%로 낮춘다. C2는 Utility를 95.9%로 회복하는 대신 ASR이 23.3%로 오른다. 그 차이가 ESCALATE 회색 지대이며, C4(판정 LLM)가 이 구간을 메우는 것이 목표다. 게이트 판정 지연은 호출당 평균 0.74 ms(p95 3.47 ms)로, LLM 호출 한 번(수백 ms 이상)에 비해 무시할 수 있다. TaskSpec 추출과 판정 LLM의 지연·토큰은 키를 넣은 뒤 측정된다.

## 6. 실패 분석

- **탐지 실패(정책 설계)**: 인젝션이 사용자가 지목한 출처 안에 있으면 출처 기반 판정이 약해진다. slack injection_task_1·3(지목 웹 페이지·채널 목록 안의 링크), travel injection_task_0·4(지목 호텔 리뷰 안의 호텔)가 그 예다. banking은 같은 이유로 대부분 ESCALATE에 머문다(lenient는 잡지만 strict는 놓침).
- **같은 권한 범위의 공격**: 사용자가 일정 생성을 요청한 태스크에서 공격도 일정 생성이면 op-level 정책(P9)이 구분하지 못한다(travel injection_task_2).
- **정당한 목적지와 같은 공격 목적지**: travel user_task_3의 `janeLong@google.com`.
- **측정상 주의**: slack injection_task_5는 AgentDojo가 시도 기록으로 성공을 판정하므로, 모든 시도를 막아도 native ASR에는 성공으로 남는다(executed-only로 보정해 함께 보고).
- **오탐 원인**: 지목 출처 내용을 송금 메모로 복사(P2), 2단계 출처(지목 메시지 → 웹 페이지 → 이메일)를 OTHER_TOOL로 판정(P1), 위임형 프롬프트의 requested_ops 공백(P6). 나머지는 설계상 ESCALATE다.
- **경로 우회**: 대역 에이전트는 우회를 시도하지 않으므로 측정하지 못했다. 실제 모델 실행에서 (c) 분류로 집계된다.
- 개선안 6개는 `docs/stage5_replay_report.md` 7절(정책 수정 제안 v2)에 있으며 승인 전이라 적용하지 않았다.

## 7. 한계

- **정책 도출·평가 데이터 중복**: 정책과 분류를 같은 35개 인젝션 태스크로 설계하고 평가했다.
- **정답 궤적 재생의 한계**: 재생은 "넘어간 에이전트가 정답대로 공격한다"는 가정이다. 실제 모델의 다른 경로는 반영되지 않는다.
- **유형 H는 범위 밖**: 툴 호출 없이 응답 텍스트만 조작하는 공격(travel injection_task_6)은 툴 호출 게이트로 막을 수 없다.
- **적응형 공격 미평가**: 정책을 아는 공격자(예: 지목 출처에만 주입, 값 쪼개기, 프롬프트에 있는 목적지 악용)는 평가하지 않았다.
- **판정 LLM 자체의 인젝션 노출**: 판정기는 주입 문장이 포함될 수 있는 주변 문맥을 본다. 인용 블록과 시스템 지시로 완화했지만 실측하지 않았다. 또한 비가역 툴은 A 판정이어도 HUMAN으로 보낸다.
- **실제 LLM 미실행**: API 키가 없어 RQ2의 모델 행동·비용, LLM TaskSpec 추출 오류의 영향, C4를 측정하지 못했다.
- **원본 정책 사양 부재**: 저장소에 `docs/policy_spec.md`가 없어 Stage 지시서의 조건으로 사양을 다시 작성했다. 원본과 다른 부분이 있을 수 있다.

## 8. 재현 방법

- 버전: Python ≥3.10 (실행 환경 3.13), `agentdojo==0.1.35`, 벤치마크 `v1.2.2`, 정책 설정 `config/policies.yaml` (version v1).
- 설정: `config/policies.yaml`, `config/tool_roles.yaml`, `config/named_source_rules.yaml`, `config/sensitive_reads.yaml`, oracle spec `data/specs/oracle/`.
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q                                   # unit + integration tests
python scripts/smoke_test.py
python scripts/build_catalog.py && python scripts/analyze_provenance.py      # Stage 1
python scripts/review_oracle.py                                             # Stage 4 review table
python scripts/replay_eval.py --spec oracle --out results/replay/v1/oracle  # Stage 5
python scripts/summarize_replay.py --run results/replay/v1/oracle
python scripts/live_eval.py --agent scripted --spec oracle --conditions C0 C1 C2 C3 --out results/live/v1/scripted
python scripts/check_enforcement.py --run results/live/v1/scripted && python scripts/summarize_live.py --run results/live/v1/scripted
python scripts/build_report.py
# with API keys in .env:
python scripts/extract_specs.py && python scripts/compare_specs.py --model <model>
python scripts/judge_eval.py
python scripts/live_eval.py --agent llm --spec llm --pilot --conditions C0 C1 --out results/live/v1/<model>
```
