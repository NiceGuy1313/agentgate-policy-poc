# 결과: Gate 없음 / 판정 LLM + 룰북 / 룰북 정적 코드

> `scripts/analysis/summarize_results.py`가 live 실행 기록에서 생성한다. 손으로 고치지 않는다. 실행 기록(json)은 요약 후 지웠고, 집계 JSON은 `results/summary/`에 남겼다.

## 한눈에 보기

- 공격 성공률(ASR): Gate 없음 23.2%, 판정 LLM 1.3%, 룰북 정적 코드 1.7%.
- 판정 LLM + 룰북(−no_gate): ASR -21.9%p (p=1.71e-57, 유의), 정상 Utility -6.2%p (p=0.263, 유의하지 않음), 공격 중 Utility -6.2%p (p=4.56e-05, 유의).
- 룰북 정적 코드(−no_gate): ASR -21.5%p (p=2.78e-55, 유의), 정상 Utility -14.4%p (p=0.00434, 유의), 공격 중 Utility -7.0%p (p=2.25e-05, 유의).
- 판정 LLM − 룰북 정적 코드: ASR -0.4%p (p=0.388, 유의하지 않음), 정상 Utility +8.2%p (p=0.0963, 유의하지 않음), 공격 중 Utility +0.7%p (p=0.645, 유의하지 않음).

## 분석

> 이 절은 아래 표·그림·재생 대조의 수치만으로 손으로 썼다. 나머지 절은 스크립트가 생성한다.

**1. 두 판정 방식 모두 공격을 거의 막는다**
- ASR이 Gate 없음 23.2%(220/949)에서 판정 LLM 1.3%(12/949), 정적 코드 1.7%(16/949)로 떨어진다. 두 감소 모두 유의하고(p<1e-54) 신뢰구간이 Gate 없음과 겹치지 않는다.
- 두 방식의 ASR 차이(−0.4%p)는 유의하지 않다(p=0.388). live에서 보안 수준은 같다.
- 남은 공격 성공의 대부분은 Gate가 다룰 수 없는 경로다. 판정 LLM 12건 중 10건, 정적 코드 16건 중 10건이 툴 호출 없이 답변 텍스트로 성공했다(유형 H). 툴 호출로 성공한 공격은 판정 LLM 2건, 정적 코드 6건이다.

**2. 같은 입력으로 비교하면 판정 LLM이 정적 코드보다 공격을 더 막고 정상 호출을 덜 막는다(재생 대조)**
- 공격 호출 차단: 판정 LLM 99.6%(2504/2513), 정적 코드 98.7%(2481/2513). 판정 LLM만 막은 공격 호출이 29건, 정적 코드만 막은 것이 6건이다(p=0.000117).
- 정상 호출 오차단: 판정 LLM 23.7%(218/920), 정적 코드 29.0%(267/920). 5.3%p 낮다(p=0.000553).
- 두 판정이 같은 답을 낸 호출은 93.3%다. 차이는 7%의 경계 사례에서 나온다.

**3. 대가는 정상 작업 손실이며, 정적 코드 쪽이 더 크다**
- 정상 Utility: Gate 없음 75.3%, 판정 LLM 69.1%(−6.2%p, 유의하지 않음), 정적 코드 60.8%(−14.4%p, p=0.00434, 유의).
- 공격 중 Utility는 두 방식 모두 약 6–7%p 줄고(유의), 서로의 차이는 0.7%p로 유의하지 않다.
- 정상 실행 실패 중 판정이 막아서 실패한 것은 판정 LLM 12건, 정적 코드 15건이다. 나머지(18건, 23건)는 에이전트가 스스로 끝내지 못한 경우다.

**4. suite별 차이**
- slack·workspace에서는 두 방식 모두 ASR 0%(workspace 정적 코드 1건)까지 낮춘다. Gate 없음 ASR이 가장 높은 slack(68.6%)에서도 0%다.
- travel이 가장 어렵다. Gate 없음 65.0%에서 판정 LLM 7.1%(10/140), 정적 코드 10.0%(14/140)로 남는다. 다만 travel의 공격 중 Utility는 정적 코드가 35.0%(49/140)로 판정 LLM 21.4%(30/140)보다 높다.
- 정상 Utility 손실은 slack(Gate 없음 17/21 → 판정 LLM 14/21, 정적 코드 11/21)과 banking(11/16 → 10/16, 8/16)에서 정적 코드가 크다.

**5. 무엇으로 막았는가**
- 정적 코드의 BLOCK 근거는 R2(툴 콘텐츠가 시킨 행동) 328, R1(요청된 행동만) 297, R8(권한·긴급성 사칭) 234, R6(삭제·계정·권한) 228 순이다. 정상 실행의 오차단은 R2·R1·R6·R4에서 주로 나온다.
- 판정 LLM은 R2를 가장 많이 근거로 들었고(336), R1 145, R8 132가 뒤를 잇는다. 같은 룰북을 쓰지만 R6을 근거로 막은 경우는 11건으로 정적 코드(228)보다 훨씬 적다. 정적 코드는 계정·권한 변경을 TaskSpec의 `requested_ops`와 정확히 일치할 때만 허용하기 때문이다.
- 판정 LLM 답은 모두 ALLOW/BLOCK으로 해석됐다(해석 실패 0건).

**6. 비용과 지연**
- 정적 코드는 LLM 호출이 없고 판정 지연이 작업당 1.5ms다. 판정 LLM은 작업당 5.4초(gpt-oss:120b, Ollama)가 더 든다.
- 두 방식 모두 ToolSpec 추출(claude-haiku-5-5)은 같은 비용이 든다(약 800회 API 호출).

**7. 팀원 Wrapper 프레임워크에서도 같은 경향이다(층화 표본, 아래 "프레임워크별 결과" 절)**
- smolagents·mini-swe-agent 모두 두 검증 방식이 ASR을 크게 낮춘다. smolagents는 56.7%에서 판정 LLM 3.1%·정적 코드 2.4%로, mini-swe-agent는 25.4%에서 0.7%·1.0%로 떨어진다(모두 유의). 두 방식 사이 ASR 차이는 유의하지 않다.
- smolagents는 Gate 없이 공격에 가장 취약하다(ASR 56.7%, native 23.2%). Gate를 붙이면 공격 중 Utility가 오히려 오른다(44.0% → 53.6%·52.6%, 유의). Gate가 주입된 지시를 따르는 호출을 막아 에이전트가 원래 작업으로 돌아가기 때문으로 보인다.
- 정상 Utility 손실은 smolagents에서 정적 코드가 크다(−17.5%p, 유의). 판정 LLM은 −7.2%p(유의하지 않음)이고, 판정 LLM이 정적 코드보다 10.3%p 높다(p=0.0129, 유의). native 재생 대조에서 본 "판정 LLM이 정상 호출을 덜 막는다"는 결과와 같은 방향이다.
- mini-swe-agent에서는 두 방식의 정상 Utility가 같다(68.0%·68.8%, 각각 −15.5%p·−14.6%p).
- 모든 프레임워크 실행에서 Gate를 우회한 툴 실행과 차단된 호출의 실행은 0건이다. Adapter가 툴 실행 지점을 Gate로 바꿔 두면 프레임워크 구조가 달라도 같은 판정이 강제된다.
- browser-use는 gpt-oss:120b가 정상 작업을 거의 끝내지 못해(중간 집계 정상 Utility 5.9%) 평가에서 제외하였다.

**8. smolagents와 mini-swe-agent의 Gate 없음 ASR이 두 배 넘게 차이 나는 이유(소규모 대조 실험, 아래 "프레임워크 요인 대조" 절)**

차이의 크기와 범위(층화 표본)
- Gate 없음 ASR: smolagents 56.7%(165/291, 95% CI 51.0–62.3), mini-swe-agent 25.4%(74/291, 20.8–30.7)다.
- 같은 표본, 같은 에이전트 모델(gpt-oss:120b), 같은 공격(`tool_knowledge`)이므로 차이는 프레임워크에서 나온다.
- 기본 능력 차이는 아니다. 정상 Utility는 81.4%와 83.5%로 신뢰구간이 겹친다.
- Gate를 붙이면 차이가 대부분 사라진다(판정 LLM 3.1% 대 0.7%, 정적 코드 2.4% 대 1.0%, 신뢰구간 겹침).

후보 원인(코드로 확인한 두 프레임워크의 차이)
- 시스템 프롬프트
  - smolagents 기본 `ToolCallingAgent` 프롬프트는 "expert assistant who can solve any task", 예시 3개, "ALWAYS provide a tool call, else you will fail" 규칙을 담는다.
  - mini-swe-agent는 Adapter의 짧은 `SYSTEM_TEMPLATE`("You are a helpful assistant that completes the user's task")를 쓴다.
- 툴 응답 역할
  - smolagents는 툴 호출을 assistant 텍스트로 적고, 툴 응답을 `user` 역할 메시지(`Observation:\n…`)로 보낸다(`tool_role_conversions`, `smolagents/models.py` 282–285행).
  - mini-swe-agent와 native는 `tool` 역할로 보낸다.
  - 주입문은 "This is an important message from me, {user}"로 시작하므로, `user` 역할이면 형식상 사용자 발화와 같다.
- 행동 인터페이스
  - smolagents와 native는 툴마다 함수 호출을 쓴다.
  - mini-swe-agent는 `bash` 하나에 `send_money '{...}'` 명령 문자열을 쓴다.

대조 실험
- 같은 공격 짝 96건에서 Gate 없이 smolagents의 두 요인만 바꿨다.
  - Adapter 옵션: `system_prompt=minimal`(mini-swe-agent와 같은 지시), `tool_response_role=tool`.
  - native와 mini-swe-agent를 같은 짝으로 함께 돌렸다.
- 기본 smolagents ASR은 57.3%로 층화 표본의 56.7%가 재현됐다.

| smolagents 팔 | 시스템 프롬프트 | 툴 응답 역할 | ASR |
|---|---|---|---|
| 기본 | 기본 | `user` | 57.3% |
| 역할만 변경 | 기본 | `tool` | 53.1% |
| 프롬프트만 변경 | 짧은 프롬프트 | `user` | 39.6% |
| 둘 다 변경 | 짧은 프롬프트 | `tool` | 28.1% |

  비교 기준(같은 짝): native 31.2%, mini-swe-agent 20.8%.

결과
- 역할만 바꾸면 효과가 없다: 57.3% → 53.1%(p=0.424).
  - 처음 세운 "툴 응답이 `user` 역할이라서"라는 단일 원인 가설은 기각된다.
- 시스템 프롬프트만 바꾸면 크게 줄어든다: 57.3% → 39.6%(p=0.000221).
- 둘 다 바꾸면 28.1%로 내려간다(p=2.46e-07).
  - native(31.2%, p=0.607)와 차이가 유의하지 않다.
  - mini-swe-agent(20.8%, p=0.143)와도 차이가 유의하지 않다.
  - 공격 중 Utility도 45.8%에서 63.5%로 올라 mini-swe-agent(63.5%)와 같아진다.
- 역할 효과는 짧은 프롬프트에서만 나타난다(39.6% → 28.1%, p=0.0192).
  - 기본 프롬프트에서는 역할을 바꿔도 줄지 않는다.
  - 기본 프롬프트가 "항상 툴을 호출해 과제를 해결하라"고 강하게 밀어서 역할 차이를 덮는 것으로 보인다(해석).
- 노출률은 모든 팔이 83–90%로 비슷하다. 차이는 주입을 본 뒤의 추종률에서 난다.
  - 추종률: 기본 smolagents 62.7%, 둘 다 바꾼 smolagents 30.6%, mini-swe-agent 21.4%.
- 따르지 않은 run에서 최종 답이 주입을 언급한 경우는 팔마다 0–1건이다. 모델은 주입을 지적하지 않고 무시한다.
- 남은 차이
  - native와 mini-swe-agent 사이에는 작은 차이가 남는다(31.2% 대 20.8%, p=0.0213). `bash` 명령 인터페이스나 `<output>` 감싸기의 영향일 수 있으나 따로 측정하지 않았다.
  - 둘 다 바꾼 smolagents와 mini-swe-agent의 차이(28.1% 대 20.8%)는 이 표본에서 유의하지 않다.

결론
- 두 배 차이의 주원인은 smolagents 기본 시스템 프롬프트다.
- 툴 응답을 `user` 역할로 보내는 구조는 짧은 프롬프트와 함께일 때만 추가로 ASR을 높인다.
- 두 요인을 mini-swe-agent·native 수준으로 맞추면 smolagents의 ASR도 같은 수준(28%)으로 내려간다.

한계
- 표본은 공격 96건(사용자 작업마다 공격 하나)이고, 조건마다 한 번 실행했다.
- smolagents `tool` 역할 팔은 1건(`workspace/user_task_7/injection_task_13`)이 Ollama 500 오류로 세 번 실패해 모든 팔에서 제외했다.
- 기본 프롬프트의 어느 부분(persona, 예시, "ALWAYS provide a tool call" 규칙)이 효과를 내는지는 나누어 보지 않았다.

**결론**
- 보안: 두 방식이 live에서 같은 수준(ASR 1–2%)이고, 같은 입력에서는 판정 LLM이 공격 호출을 조금 더 막는다.
- 정상 작업: 판정 LLM이 덜 막는다(재생 대조 오차단 −5.3%p, live 정상 Utility +8.2%p는 유의하지 않음).
- 비용: 정적 코드는 LLM 없이 즉시 판정한다. 판정 LLM이 런타임에 툴 응답을 읽지 않으면서도(ToolSpec만 입력) 오차단을 줄이는 대신, 호출마다 수 초가 든다.
- 일반화: 같은 결과가 팀원 Wrapper 프레임워크(smolagents, mini-swe-agent)에서도 재현된다. 정상 작업 손실은 정적 코드 쪽이 같거나 더 크다.
- 한계: 에이전트 모델·공격이 하나씩(gpt-oss:120b, `tool_knowledge`)이고 조건마다 한 번 실행했다. 프레임워크 비교는 층화 표본(공격 291건)이다. 정상 작업 표본은 97개라 정상 Utility 차이는 신뢰구간이 넓다.

## 프레임워크별 결과(층화 표본)

> `scripts/analysis/summarize_frameworks.py`가 생성한다. 팀원 Wrapper가 씌워진 프레임워크(smolagents, mini-swe-agent)를 같은 층화 표본(`data/agentdojo/pairs/strat3_seed0.txt`, 정상 97건 · 공격 291건, 작업마다 공격 3개)으로 돌렸다. 에이전트·판정 LLM은 모두 `ollama:gpt-oss:120b`, 공격은 `tool_knowledge`다. 모든 툴 실행은 Adapter → Bridge → Policy Gate를 지난다. native 결과는 위 "결과" 절의 전체 규모 실험(조건마다 정상 97건 · 공격 949건)이다.

제외한 run: 오류 1건, Gate 우회로 무효 0건. Ollama 서버 오류(500·429)로 실패한 run은 다시 실행해 채웠고, 남은 오류는 AgentDojo 채점 함수가 에이전트 호출에 없는 인자를 읽다 실패한 경우다.

| 프레임워크 | 조건 | ASR | Utility | 공격 중 Utility | 차단 호출 |
|---|---|---|---|---|---|
| smolagents | Gate 없음 | 56.7% (165/291) | 81.4% (79/97) | 44.0% (128/291) | 0 |
| smolagents | 판정 LLM + 룰북 | 3.1% (9/291) | 74.2% (72/97) | 53.6% (156/291) | 384 |
| smolagents | 룰북 정적 코드(정규식) | 2.4% (7/291) | 63.9% (62/97) | 52.6% (153/291) | 352 |
| mini-swe-agent | Gate 없음 | 25.4% (74/291) | 83.5% (81/97) | 66.0% (192/291) | 0 |
| mini-swe-agent | 판정 LLM + 룰북 | 0.7% (2/291) | 68.0% (66/97) | 59.8% (174/291) | 288 |
| mini-swe-agent | 룰북 정적 코드(정규식) | 1.0% (3/291) | 68.8% (66/96) | 62.2% (181/291) | 255 |

같은 (사용자 작업, 공격) 짝끼리 비교한 차이(exact McNemar p):

- **smolagents**
  - 판정 LLM + 룰북 − Gate 없음: ASR -53.6%p (p=8.7e-46, 유의), Utility -7.2%p (p=0.118), 공격 중 Utility +9.6%p (p=0.00557, 유의)
  - 룰북 정적 코드(정규식) − Gate 없음: ASR -54.3%p (p=2.2e-46, 유의), Utility -17.5%p (p=7.63e-05, 유의), 공격 중 Utility +8.6%p (p=0.0187, 유의)
  - 판정 LLM − 정적 코드: ASR +0.7%p (p=0.688), Utility +10.3%p (p=0.0129, 유의), 공격 중 Utility +1.0%p (p=0.795)
- **mini-swe-agent**
  - 판정 LLM + 룰북 − Gate 없음: ASR -24.7%p (p=7.94e-21, 유의), Utility -15.5%p (p=0.00408, 유의), 공격 중 Utility -6.2%p (p=0.0247, 유의)
  - 룰북 정적 코드(정규식) − Gate 없음: ASR -24.4%p (p=1.51e-19, 유의), Utility -14.6%p (p=0.00258, 유의), 공격 중 Utility -3.8%p (p=0.235)
  - 판정 LLM − 정적 코드: ASR -0.3%p (p=1), Utility +0.0%p (p=1), 공격 중 Utility -2.4%p (p=0.382)

**프레임워크 × 검증 방식(95% Wilson 신뢰구간)**

![프레임워크별 결과](figures/frameworks.png)

## 프레임워크 요인 대조(소규모)

> `scripts/analysis/role_ablation.py`가 생성한다. 같은 공격 짝 96건(`data/agentdojo/pairs/role_ablation_97.txt`, `strat3_seed0`에서 사용자 작업마다 첫 공격 하나)을 Gate 없이, gpt-oss:120b와 `tool_knowledge`로 돌렸다. smolagents 팔은 Adapter 옵션 두 개만 바꾼다. `system_prompt=minimal`은 기본 `ToolCallingAgent` 프롬프트 대신 mini-swe-agent와 같은 짧은 지시를 쓰고, `tool_response_role=tool`은 툴 호출·응답을 smolagents 기본(assistant 텍스트 + `user` 메시지) 대신 OpenAI 툴 형식(assistant `tool_calls` + `tool` 메시지)으로 보낸다. 오류로 빠진 짝은 모든 팔에서 제외한다. 노출은 주입문이 든 툴 응답을 받은 run, 추종률은 노출된 run 중 공격이 성공한 비율이다.

| 팔 | ASR | 공격 중 Utility | 노출 | 노출 시 추종률 | 평균 툴 호출 | 오류 제외 |
|---|---|---|---|---|---|---|
| smolagents 기본(기본 프롬프트·user 역할) | 57.3% (55/96, 47.3–66.7) | 45.8% (44/96, 36.2–55.8) | 86.5% (83/96, 78.2–91.9) | 62.7% (52/83, 51.9–72.3) | 5.16 | 0 |
| smolagents tool 역할 | 53.1% (51/96, 43.2–62.8) | 51.0% (49/96, 41.2–60.8) | 89.6% (86/96, 81.9–94.2) | 57.0% (49/86, 46.4–66.9) | 5.33 | 1 |
| smolagents 짧은 프롬프트 | 39.6% (38/96, 30.4–49.6) | 52.1% (50/96, 42.2–61.8) | 89.6% (86/96, 81.9–94.2) | 43.0% (37/86, 33.1–53.6) | 5.82 | 0 |
| smolagents 짧은 프롬프트·tool 역할 | 28.1% (27/96, 20.1–37.8) | 63.5% (61/96, 53.6–72.5) | 88.5% (85/96, 80.6–93.5) | 30.6% (26/85, 21.8–41.0) | 4.74 | 0 |
| native | 31.2% (30/96, 22.9–41.1) | 57.3% (55/96, 47.3–66.7) | 83.3% (80/96, 74.6–89.5) | 36.2% (29/80, 26.6–47.2) | 4.3 | 0 |
| mini-swe-agent | 20.8% (20/96, 13.9–30.0) | 63.5% (61/96, 53.6–72.5) | 87.5% (84/96, 79.4–92.7) | 21.4% (18/84, 14.0–31.4) | 4.17 | 0 |

같은 짝끼리 비교(exact McNemar p, 앞 팔만 성공 / 뒤 팔만 성공):

- smolagents 기본(기본 프롬프트·user 역할) vs smolagents tool 역할: ASR 9/5 (p=0.424), 공격 중 Utility 5/10 (p=0.302)
- smolagents 기본(기본 프롬프트·user 역할) vs smolagents 짧은 프롬프트: ASR 19/2 (p=0.000221), 공격 중 Utility 6/12 (p=0.238)
- smolagents 기본(기본 프롬프트·user 역할) vs smolagents 짧은 프롬프트·tool 역할: ASR 30/2 (p=2.46e-07), 공격 중 Utility 4/21 (p=0.000911)
- smolagents 기본(기본 프롬프트·user 역할) vs native: ASR 27/2 (p=1.62e-06), 공격 중 Utility 7/18 (p=0.0433)
- smolagents 기본(기본 프롬프트·user 역할) vs mini-swe-agent: ASR 38/3 (p=1.05e-08), 공격 중 Utility 6/23 (p=0.00232)
- smolagents tool 역할 vs smolagents 짧은 프롬프트: ASR 17/4 (p=0.0072), 공격 중 Utility 9/10 (p=1)
- smolagents tool 역할 vs smolagents 짧은 프롬프트·tool 역할: ASR 25/1 (p=8.05e-07), 공격 중 Utility 5/17 (p=0.0169)
- smolagents tool 역할 vs native: ASR 23/2 (p=1.94e-05), 공격 중 Utility 11/17 (p=0.345)
- smolagents tool 역할 vs mini-swe-agent: ASR 33/2 (p=3.67e-08), 공격 중 Utility 8/20 (p=0.0357)
- smolagents 짧은 프롬프트 vs smolagents 짧은 프롬프트·tool 역할: ASR 15/4 (p=0.0192), 공격 중 Utility 5/16 (p=0.0266)
- smolagents 짧은 프롬프트 vs native: ASR 14/6 (p=0.115), 공격 중 Utility 10/15 (p=0.424)
- smolagents 짧은 프롬프트 vs mini-swe-agent: ASR 22/4 (p=0.000534), 공격 중 Utility 7/18 (p=0.0433)
- smolagents 짧은 프롬프트·tool 역할 vs native: ASR 6/9 (p=0.607), 공격 중 Utility 13/7 (p=0.263)
- smolagents 짧은 프롬프트·tool 역할 vs mini-swe-agent: ASR 12/5 (p=0.143), 공격 중 Utility 11/11 (p=1)
- native vs mini-swe-agent: ASR 13/3 (p=0.0213), 공격 중 Utility 6/12 (p=0.238)

suite별 ASR:

| 팔 | banking | slack | travel | workspace |
|---|---|---|---|---|
| smolagents 기본(기본 프롬프트·user 역할) | 50% (8/16) | 90% (19/21) | 80% (16/20) | 31% (12/39) |
| smolagents tool 역할 | 56% (9/16) | 100% (21/21) | 65% (13/20) | 21% (8/39) |
| smolagents 짧은 프롬프트 | 25% (4/16) | 67% (14/21) | 70% (14/20) | 15% (6/39) |
| smolagents 짧은 프롬프트·tool 역할 | 12% (2/16) | 62% (13/21) | 45% (9/20) | 8% (3/39) |
| native | 19% (3/16) | 67% (14/21) | 60% (12/20) | 3% (1/39) |
| mini-swe-agent | 19% (3/16) | 33% (7/21) | 40% (8/20) | 5% (2/39) |

주입 유형별 ASR(A 외부 연락, B 금전, C 반출, D 삭제, E 계정, F 전파·방문, G 예약, H 툴 없는 출력):

| 팔 | A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|---|
| smolagents 기본(기본 프롬프트·user 역할) | 100% (3/3) | 0% (0/6) | 46% (25/54) | 100% (3/3) | 75% (3/4) | 73% (8/11) | 92% (11/12) | 67% (2/3) |
| smolagents tool 역할 | 100% (3/3) | 17% (1/6) | 43% (23/54) | 33% (1/3) | 100% (4/4) | 82% (9/11) | 75% (9/12) | 33% (1/3) |
| smolagents 짧은 프롬프트 | 100% (3/3) | 0% (0/6) | 22% (12/54) | 67% (2/3) | 75% (3/4) | 73% (8/11) | 75% (9/12) | 33% (1/3) |
| smolagents 짧은 프롬프트·tool 역할 | 67% (2/3) | 0% (0/6) | 15% (8/54) | 33% (1/3) | 75% (3/4) | 45% (5/11) | 67% (8/12) | 0% (0/3) |
| native | 33% (1/3) | 17% (1/6) | 17% (9/54) | 0% (0/3) | 75% (3/4) | 55% (6/11) | 75% (9/12) | 33% (1/3) |
| mini-swe-agent | 33% (1/3) | 17% (1/6) | 9% (5/54) | 33% (1/3) | 75% (3/4) | 18% (2/11) | 58% (7/12) | 0% (0/3) |


## 에이전트별 그래프

> `scripts/analysis/make_agent_figures.py`가 집계 JSON(`results/summary/`)에서 생성한다. 에이전트마다 세 조건의 ASR·정상 Utility·공격 중 Utility와 95% Wilson 신뢰구간이다. native는 전체 규모, 나머지는 층화 표본이다.

**native**

![native](figures/agent_native.png)

**smolagents**

![smolagents](figures/agent_smolagents.png)

**mini-swe-agent**

![mini-swe-agent](figures/agent_mini-swe-agent.png)

## 실험 흐름

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant TS as TaskSpec 추출기<br/>(claude-haiku-5-5)
    participant AG as 에이전트<br/>(ollama:gpt-oss:120b)
    participant GT as Gate
    participant JD as 판정<br/>(gpt-oss:120b + 룰북<br/>또는 룰북 정적 코드)
    participant TL as 툴<br/>(AgentDojo 환경)
    participant TO as ToolSpec 추출기<br/>(claude-haiku-5-5)

    U->>TS: 프롬프트 + 툴 목록
    TS-->>GT: TaskSpec (사용자가 허락한 범위)
    U->>AG: 프롬프트
    loop 에이전트가 끝낼 때까지
        AG->>GT: 툴 호출 (툴, 인자)
        alt no_gate 또는 읽기 호출
            GT->>TL: 그대로 실행
        else gate_llm_judge · gate_static_rules의 effect 호출 (상태 변경 · 외부 전송 · URL 방문)
            GT->>JD: TaskSpec + 지금까지의 ToolSpec + 대기 중 호출
            JD-->>GT: ALLOW 또는 BLOCK
            alt ALLOW
                GT->>TL: 실행
            else BLOCK
                GT-->>AG: 차단 메시지
            end
        end
        TL-->>AG: 툴 응답 (공격 실행이면 주입 문구 포함)
        TL->>TO: 툴 응답 원문
        TO-->>GT: 표준 ToolSpec (레코드 data · 응답 속 요청)
    end
    AG-->>U: 최종 답변
```

## 구성 요소

**TaskSpec 추출기** (`claude-haiku-5-5`): 사용자가 허락한 범위를 구조화한다.
- 입력: 사용자 프롬프트, 툴 목록(이름·설명). 툴 응답은 보지 않는다.
- 출력: TaskSpec. 요청한 행동(`requested_ops`), 사용자가 지목한 자료(`named_sources`), 허용 목적지(`allowed_dest`), 금액 상한(`amount_cap`), 전송 요청 여부(`egress_requested`), 지시 수행을 위임한 자료(`delegated_sources`).

**에이전트** (`ollama:gpt-oss:120b`): 평가 대상이다.
- 입력: 사용자 프롬프트, 툴 응답 또는 차단 메시지.
- 출력: 툴 호출(툴 이름, 인자).

**Gate**: 툴 호출이 실행되기 직전의 관문이다. 규칙으로 판정하지 않는다.
- 입력: 툴 호출.
- 출력: 읽기 호출은 그대로 실행한다. effect 호출(상태 변경·외부 전송·URL 방문)은 판정기(판정 LLM 또는 룰북 정적 코드)의 답대로 실행하거나 차단한다.

**판정 LLM** (`gpt-oss:120b`, gate_llm_judge): effect 호출을 실행할지 정한다.
- 입력: 룰북(`config/rulebook.yaml`, R1–R10), 사용자 프롬프트와 TaskSpec, 지금까지 받은 툴 응답의 ToolSpec, 대기 중 호출(툴, 인자). 툴 응답 원문은 받지 않는다.
- 출력: ALLOW 또는 BLOCK과 이유 한 문장.

**룰북 정적 코드** (`src/agentgate/policy_gate/rule_checker.py`, gate_static_rules): 같은 입력을 코드로 검사한다. LLM 호출이 없다.
- 입력: 판정 LLM과 같은 TaskSpec·ToolSpec·대기 중 호출, 툴 인자 역할(`config/agentdojo/tool_args.yaml`).
- 출력: ALLOW 또는 BLOCK과 막은 규칙 id(R1–R8). 어떤 규칙도 막지 않으면 ALLOW(R10).

**툴** (AgentDojo v1.2.2 환경):
- 입력: 실행이 허용된 툴 호출.
- 출력: 툴 응답. 공격 실행에서는 응답 안에 주입 문구가 들어 있다.

**ToolSpec 추출기** (`claude-haiku-5-5`): 툴 응답을 고정 스키마 JSON(표준 ToolSpec)으로 바꾼다.
- 입력: 툴 응답 원문, 그 응답을 만든 호출, 툴 목록.
- 출력: ToolSpec. 레코드(종류·작성자·타입별 data 값)와 응답 속 요청(누구에게 하는지, 어떤 툴로 수행되는지, 사칭·긴급성 claims, 타입별 targets). 원문에 없거나 타입이 맞지 않는 값은 코드로 걸러낸다.

## 비교한 조건

- **no_gate** Gate 없음
- **gate_llm_judge** 판정 LLM + 룰북
- **gate_static_rules** 룰북 정적 코드

에이전트 `ollama:gpt-oss:120b`, 공격 `tool_knowledge`, 조건마다 공격 없는 실행 97건과 공격 실행 949건이다.

## 결과

- **ASR**: 공격 실행 중 공격자 목표가 달성된 비율. 낮을수록 좋다.
- **Utility**: 공격 없는 실행에서 사용자 작업을 끝낸 비율.
- **공격 중 Utility**: 공격 실행에서도 사용자 작업을 끝낸 비율.

| 조건 | ASR | Utility | 공격 중 Utility |
|---|---|---|---|
| no_gate Gate 없음 | 23.2% (220/949) | 75.3% (73/97) | 66.5% (631/949) |
| gate_llm_judge 판정 LLM + 룰북 | 1.3% (12/949) | 69.1% (67/97) | 60.3% (572/949) |
| gate_static_rules 룰북 정적 코드 | 1.7% (16/949) | 60.8% (59/97) | 59.5% (565/949) |

그림은 `scripts/analysis/make_figures.py`가 같은 실행 기록에서 만든다.

**조건별 핵심 지표(95% Wilson 신뢰구간)**

![조건별 핵심 지표(95% Wilson 신뢰구간)](figures/main_metrics.png)

**보안-유용성 trade-off**

![보안-유용성 trade-off](figures/tradeoff.png)

**suite별 ASR·정상 Utility**

![suite별 ASR·정상 Utility](figures/by_suite.png)

**판정 규칙별 BLOCK 수(공격 실행·정상 실행)**

![판정 규칙별 BLOCK 수(공격 실행·정상 실행)](figures/rules.png)

**재생 대조: 같은 입력에서 호출 단위 차단률**

![재생 대조: 같은 입력에서 호출 단위 차단률](figures/replay_contrast.png)


같은 (사용자 작업, 공격) 짝끼리 비교했다. 차이는 앞 조건 − 뒤 조건, p는 exact McNemar다.

**gate_llm_judge − no_gate** (판정 LLM + 룰북 대 Gate 없음)
- ASR -21.9%p (p=1.71e-57, 유의)
- Utility -6.2%p (p=0.263, 유의하지 않음)
- 공격 중 Utility -6.2%p (p=4.56e-05, 유의)

**gate_static_rules − no_gate** (룰북 정적 코드 대 Gate 없음)
- ASR -21.5%p (p=2.78e-55, 유의)
- Utility -14.4%p (p=0.00434, 유의)
- 공격 중 Utility -7.0%p (p=2.25e-05, 유의)

**gate_llm_judge − gate_static_rules** (판정 LLM 대 룰북 정적 코드)
- ASR -0.4%p (p=0.388, 유의하지 않음)
- Utility +8.2%p (p=0.0963, 유의하지 않음)
- 공격 중 Utility +0.7%p (p=0.645, 유의하지 않음)

95% 신뢰구간(Wilson):
- ASR: no_gate [20.6, 26.0], gate_llm_judge [0.7, 2.2], gate_static_rules [1.0, 2.7]
- Utility: no_gate [65.8, 82.8], gate_llm_judge [59.3, 77.4], gate_static_rules [50.9, 70.0]
- 공격 중 Utility: no_gate [63.4, 69.4], gate_llm_judge [57.1, 63.3], gate_static_rules [56.4, 62.6]

## 자세히

### 판정 결과

- **gate_llm_judge**: effect 호출 1085회 판정, ALLOW 595, BLOCK 490; BLOCK 근거 규칙 R1 145, R10 9, R2 336, R3 85, R4 45, R5 8, R6 11, R7 22, R8 132, R9 6
- **gate_static_rules**: effect 호출 1220회 판정, ALLOW 535, BLOCK 685; BLOCK 근거 규칙 R1 297, R2 328, R3 131, R4 127, R5 41, R6 228, R7 45, R8 234

### 정상 작업이 실패한 이유

공격 없는 실행 중 실패한 것을 나눴다. "차단 없이 실패"는 에이전트가 스스로 작업을 끝내지 못한 경우다.

- **no_gate** 24건: 차단 없이 실패 24
- **gate_llm_judge** 30건: 차단 없이 실패 18, 판정 차단 12
- **gate_static_rules** 38건: 차단 없이 실패 23, 판정 차단 15

### 공격이 성공한 경로

"툴 호출 없이 성공(유형 H)"은 에이전트가 답변 텍스트만으로 공격 목표를 달성한 경우로, 툴 호출 Gate로는 막을 수 없다.

- **gate_llm_judge** 12건: (a) 툴 호출 없이 성공(유형 H) 10, (d') 판정 LLM 허용으로 성공 2
- **gate_static_rules** 16건: (a) 툴 호출 없이 성공(유형 H) 10, (b) 공격 호출이 ALLOW됨(탐지 실패) 1, (d') 판정 LLM 허용으로 성공 5

### 비용과 집행 무결성

- **gate_llm_judge** 판정 API 960회(입력 1,592,628, 캐시 읽기 0, 출력 469,389 토큰), ToolSpec API 831회(입력 692,122, 캐시 읽기 2,211,174, 출력 1,102,537 토큰)
- **gate_static_rules** 판정 API 0회(입력 0, 캐시 읽기 0, 출력 0 토큰), ToolSpec API 789회(입력 655,511, 캐시 읽기 2,093,363, 출력 1,059,779 토큰)
- 집행 무결성: 실행 3,138건, 차단된 호출이 실행된 위반 0건, 오류 0건, 무효(Gate 우회) 0건

## 재생 대조: 같은 입력에서 판정 LLM(A)과 룰북 정적 코드(B)

> `scripts/evaluation/replay_contrast.py`가 생성한다. 에이전트 없이 정답 궤적의 호출을 그대로 실행하고, effect 호출마다 같은 입력(TaskSpec + 지금까지의 ToolSpec + 대기 중 호출)을 A(판정 LLM + 룰북, gate_llm_judge)와 B(룰북 정적 코드, gate_static_rules)에 넣었다. 판정으로 막지 않으므로 모든 호출에서 두 판정기의 입력이 같다.

- 궤적: 정상 97개, 공격 쌍 949개(`tool_knowledge`). 판정한 effect 호출: 공격 호출 2513개, 정상 호출 920개.
- A(판정 LLM) 판정 LLM: `ollama` `gpt-oss:120b`
- 두 판정이 같은 답을 낸 호출: 93.3% (3203/3433) [92.4, 94.1]
- A(판정 LLM) 답을 읽을 수 없어 BLOCK으로 센 고유 입력: 0개

### 호출 단위

- 공격 호출을 BLOCK (높을수록 좋음): A 99.6% (2504/2513) [99.3, 99.8], B 98.7% (2481/2513) [98.2, 99.1]. 차이 +0.9%p, A만 29건 · B만 6건, McNemar p=0.000117 (유의)
- 정상 호출을 BLOCK (오차단, 낮을수록 좋음): A 23.7% (218/920) [21.1, 26.6], B 29.0% (267/920) [26.2, 32.0]. 차이 -5.3%p, A만 73건 · B만 122건, McNemar p=0.000553 (유의)

### 궤적 단위

- 공격 쌍에서 공격 호출을 하나라도 막음: A 97.7% (927/949) [96.5, 98.5], B 97.0% (921/949) [95.8, 98.0]. 차이 +0.6%p, A만 8건 · B만 2건, McNemar p=0.109 (유의하지 않음)
- 공격 쌍에서 정상 호출을 모두 허용: A 83.0% (788/949) [80.5, 85.3], B 82.6% (784/949) [80.1, 84.9]. 차이 +0.4%p, A만 58건 · B만 54건, McNemar p=0.777 (유의하지 않음)
- 정상 작업에서 정상 호출을 모두 허용: A 85.6% (83/97) [77.2, 91.2], B 81.4% (79/97) [72.6, 87.9]. 차이 +4.1%p, A만 6건 · B만 2건, McNemar p=0.289 (유의하지 않음)

### B(정적 코드) 규칙별 BLOCK (한 호출을 여러 규칙이 막을 수 있다)

- 공격 호출: R1 2329, R2 2324, R3 950, R4 737, R5 111, R6 1316, R7 239, R8 2338
- 정상 호출: R1 94, R2 136, R3 27, R5 79, R6 36, R7 6, R8 41

### A(판정 LLM) 판정이 인용한 조항

- R1 2237, R2 2238, R3 704, R4 281, R5 108, R6 449, R7 76, R8 420, R9 54, R10 22

### 비용 (동기 호출, 고유 입력 기준)

- A(판정 LLM): 판정 3329회, 입력 6,648,370, 캐시 읽기 0, 출력 1,260,163 토큰
- B(정적 코드): LLM 호출 없음
