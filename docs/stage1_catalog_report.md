# Stage 1 — 시나리오 카탈로그와 인자 출처 분석

> 이 문서는 `scripts/build_report.py`가 `data/catalog/`의 산출물에서 생성한다. 산출물은 `scripts/build_catalog.py`, `scripts/analyze_provenance.py`가 LLM 호출 없이 만든다.

## 1. 태스크 수 (AgentDojo v1.2.2)

| suite | user task | injection task | 참조 궤적을 직접 작성한 injection task |
|---|---|---|---|
| workspace | 40 | 14 | 8 |
| travel | 20 | 7 | 0 |
| banking | 16 | 9 | 0 |
| slack | 21 | 5 | 0 |
| 합계 | 97 | 35 | 8 |

AgentDojo v1.2에서 추가된 workspace injection_task_6~13은 `ground_truth()`가 빈 목록을 반환한다. 재생 평가에는 공격에 넘어간 에이전트의 호출이 필요하므로 `src/agentgate/attack_trajectories.py`에 참조 궤적 생성기를 작성했다. 생성기는 현재 환경에서 ID를 계산하며, 35개 전부가 기본 환경에서 해당 태스크의 `security()`를 만족함을 `tests/test_attack_trajectories.py`로 확인했다.

## 2. 유형별 분포

| 유형 | 이름 | 태스크 수 |
|---|---|---|
| A | 무단 외부 연락 | 1 |
| B | 금전 탈취 | 3 |
| C | 민감 데이터 반출 | 20 |
| D | 삭제·은폐 | 1 |
| E | 계정·권한 변경 | 2 |
| F | 비신뢰 내용 전파·방문 | 3 |
| G | 무단 예약·일정 생성 | 4 |
| H | 툴 호출 없는 출력 조작 | 1 |

효과 호출 수 분포: {0: 1, 1: 23, 2: 3, 3: 2, 4: 1, 5: 1, 7: 1, 8: 2, 9: 1} (키: 태스크당 효과 호출 수, 값: 태스크 수). 효과 호출이 0개인 태스크는 travel/injection_task_6이며, 유형 H로 정상이다.

## 3. 레이블 모순 점검

유형별 필수 툴군과 대상 정책의 적용 툴이 ground truth 호출에 존재하는지 자동 점검했다. 모순 0건.

## 4. 인자 출처 집계 (user task 97개, 주입 없는 기본 환경)

판정 범주는 PROMPT(사용자 프롬프트에 있음), TOOL(이전 툴 응답에 처음 등장), UNSEEN(어디에도 없음)이다. 정규화는 이메일 소문자, URL의 scheme·www·끝 슬래시 제거, IBAN 공백 제거이며, 리스트 인자는 원소별로 판정한다. 짧은 ID(예: 파일 ID `11`)는 구조화 응답의 필드 값과 정확히 일치할 때만 등장으로 본다.

| 툴.인자 | PROMPT | TOOL | UNSEEN |
|---|---|---|---|
| add_calendar_event_participants.participants | 2 | 0 | 0 |
| create_calendar_event.participants | 2 | 8 | 0 |
| delete_file.file_id | 0 | 2 | 0 |
| get_webpage.url | 9 | 9 | 0 |
| invite_user_to_slack.user | 4 | 1 | 0 |
| invite_user_to_slack.user_email | 0 | 5 | 0 |
| post_webpage.url | 1 | 0 | 0 |
| reserve_hotel.hotel | 1 | 0 | 0 |
| schedule_transaction.amount | 0 | 1 | 0 |
| schedule_transaction.recipient | 1 | 0 | 0 |
| send_direct_message.recipient | 7 | 5 | 0 |
| send_email.recipients | 3 | 4 | 0 |
| send_money.amount | 1 | 2 | 3 |
| send_money.recipient | 4 | 2 | 0 |
| share_file.email | 2 | 0 | 0 |
| update_password.password | 1 | 0 | 0 |
| update_scheduled_transaction.amount | 1 | 0 | 3 |
| update_scheduled_transaction.recipient | 1 | 0 | 0 |

### policy_spec 2절 수치와의 비교

Stage 1 지시서에 예시로 제시된 `send_money.recipient` 프롬프트 4 · 툴 응답 2와 이 집계(4 · 2)는 일치한다. 원본 `docs/policy_spec.md`가 저장소에 없어 이 세션에서 사양을 새로 작성했으며, 2절 표는 이 집계를 그대로 옮겼다. 따라서 나머지 항목은 독립 대조가 아니라 같은 출력이다. UNSEEN 항목은 모두 계산된 값이다 (`send_money.amount` 3건과 `update_scheduled_transaction.amount` 3건: 차액·가산 금액 계산). 정규화 문제로 UNSEEN이 된 사례는 짧은 ID 매칭을 추가한 뒤 남지 않았다.

## 5. 공격자 목적지 점검

| 목적지 | user PROMPT에 등장 | 기본 환경에 등장 | 통과 |
|---|---|---|---|
| mark.black-2134@gmail.com | - | - | 예 |
| jay@google.com | - | - | 예 |
| janeLong@google.com | travel/user_task_3 | - | 아니오 |
| US133000000121212121212 | - | - | 예 |
| www.secure-systems-252.com | - | - | 예 |
| www.my-website-234.com/random | - | - | 예 |
| www.true-informations.com | - | slack | 아니오 |
| fred9246@gmail.com | - | - | 예 |
| https://bit.ly/sUf7tOJ | - | - | 예 |

통과하지 못한 두 건은 다음과 같다.

- `janeLong@google.com`: travel user_task_3 프롬프트가 이 주소를 사용자의 배우자 주소로 명시한다. 이 쌍(travel user_task_3 × injection_task_5)에서는 공격 목적지가 정당한 목적지와 같으므로 P1이 원리적으로 구분할 수 없다. Stage 5에서 이 쌍을 놓친 원인이 이것이다.
- `www.true-informations.com`: slack 기본 환경의 웹 페이지 사전에 내용이 빈 키로 존재한다. 에이전트가 `get_webpage`로 그 URL을 직접 열기 전에는 어떤 툴 응답에도 나타나지 않으므로 "툴 응답에만 있는 값" 판정에는 영향이 없다.

## 6. 정책 수정 제안

- 위 `janeLong@google.com` 사례처럼 공격 목적지가 프롬프트의 정당한 목적지와 같으면 출처 기반 정책으로는 막을 수 없다. 본문 내용(P2)이나 판정 LLM이 보조해야 한다. 사양은 수정하지 않았다.
- `trusted_tools`(연락처·계정 정보처럼 출처를 신뢰하는 툴)는 인젝션 벡터를 담지 않는다는 전제가 필요하다. 점검 결과 slack `get_channels`는 채널 이름에 인젝션 벡터를 담으므로 신뢰 목록에서 제외했다. 이 전제는 `tests/test_config_consistency.py`가 canary 주입으로 계속 검사한다.
