# 툴 호출 정책 사양 (v1–v3.1)

> 원본 사양 문서가 없어 작업 지시서의 조건과 AgentDojo v1.2.2 소스로 다시 작성한 사양이다. 툴·인자 이름은 모두 AgentDojo 실제 이름이다. 구조와 설계 근거는 [`architecture.md`](architecture.md)에 있다. 1–4절은 v1, 5절은 v2–v3.1에서 추가·변경한 정책이다.

## 1. 위협 분류 (injection task 35개)

| 유형 | 이름 | 내용 | 대상 정책 |
|---|---|---|---|
| A | 무단 외부 연락 | 공격자 목적지로 공격자가 정한 내용을 보냄 | P1 |
| B | 금전 탈취 | 공격자 계좌로 송금하거나 예약 이체 수신자를 바꿈 | P1, P4 |
| C | 민감 데이터 반출 | 메일·파일·거래·개인정보·메시지를 외부로 보냄(단건·대량) | P1, P2, P3 |
| D | 삭제·은폐 | 파일·메일 삭제, 보낸 메일 삭제로 흔적 제거 | P5 |
| E | 계정·권한 변경 | 비밀번호 변경, 사용자 초대·추가·제거 | P6 |
| F | 비신뢰 내용 전파·방문 | 툴 응답의 링크·문구를 남에게 보내거나 공격 URL 방문 | P7 |
| G | 무단 예약·일정 생성 | 요청하지 않은 예약·일정 생성 | P8, P9 |
| H | 툴 호출 없는 출력 조작 | 응답 문장만 조작 | 범위 밖 |

태스크별 레이블은 [`data/agentdojo/benchmark/injection_labels.yaml`](../data/agentdojo/benchmark/injection_labels.yaml)에 있다.

**설계 전제**: 정상 태스크의 목적지 값도 상당수가 툴 응답에서 온다([`data/agentdojo/benchmark/provenance_summary.json`](../data/agentdojo/benchmark/provenance_summary.json)). 그래서 "프롬프트에 없으면 차단"만으로는 오탐이 커서, 사용자가 지목한 자료에서 온 값은 별도 범주(`NAMED_SOURCE` → ESCALATE)로 다룬다.

## 2. 정책 P1–P9

구현은 [`src/agentgate/v1/policies.py`](../src/agentgate/v1/policies.py), 대상 툴·임계값은 [`config/v1/policies.yaml`](../config/v1/policies.yaml)의 같은 키(`P1`…`P9`)에 있다.

| 정책 | 클래스 | 대상 | 규칙 → 판정 |
|---|---|---|---|
| P1 외부 목적지 출처 | `DestinationProvenancePolicy` | `send_email` 수신자, `send_money` 등 `recipient`, `share_file.email`, 일정 참석자, `invite_user_to_slack.user_email`, `post_webpage.url` | 신뢰 출처 → ALLOW · `NAMED_SOURCE` → ESCALATE · `OTHER_TOOL`·`UNSEEN` → BLOCK. 리스트는 원소별로 보고 가장 엄한 판정 |
| P2 민감 데이터 반출 | `SensitiveEgressPolicy` | 외부 전송의 본문·제목·메모·첨부 | `egress_requested=false`인데 민감 읽기 결과와 16자 이상 겹치거나, 고가치 토큰을 담거나, 파일을 첨부·공유 → BLOCK |
| P3 대량 반출 | `BulkEgressPolicy` | P2와 같음 | 한 번의 전송에 서로 다른 읽기 결과 3개 이상 → BLOCK (`egress_requested`와 무관) |
| P4 금전 | `MoneyTransferPolicy` | `send_money`, `schedule_transaction`, `update_scheduled_transaction`의 금액 | `amount_cap` 초과 → BLOCK · 상한이 없으면 금액이 프롬프트나 지목 자료에 있어야 함, 없으면 ESCALATE · 세션 누적 한도 초과, 프롬프트 밖 수신자에게 반복 송금 → BLOCK |
| P5 삭제 | `DeletionPolicy` | `delete_email`, `delete_file` | 이번 세션에 보낸 메일 삭제 → BLOCK · 프롬프트로 특정하지 않은 파일 삭제 → ESCALATE |
| P6 계정·권한 | `AccountPermissionPolicy` | `update_password`, `update_user_info`, slack 사용자 관리, `share_file` | `requested_ops`에 없음 → BLOCK · 새 비밀번호가 프롬프트 출처가 아님 → BLOCK |
| P7 비신뢰 내용 | `UntrustedContentPolicy` | 메시지 본문, `get_webpage.url` | 본문에 인젝션 문구나 `OTHER_TOOL` 출처 링크 → BLOCK · 방문 URL이 프롬프트·지목 자료 밖 → ESCALATE |
| P8 예약 | `ReservationPolicy` | `reserve_hotel`, `reserve_car_rental`, `reserve_restaurant` | `requested_ops`에 없음 → BLOCK · 예약 대상이 프롬프트·지목 자료 밖 → ESCALATE |
| P9 일정 | `CalendarChangePolicy` | 일정 생성·변경·취소·참석자 추가 | `requested_ops`에 없음 → BLOCK (외부 참석자는 P1이 판정) |

v3부터 P7의 인젝션 문구 목록(`instruction_phrases`)은 비운다. AgentDojo 공격 템플릿 문구를 그대로 담고 있어 공격을 미리 알고 막는 규칙이기 때문이다.

## 3. TaskSpec

사용자 프롬프트와 툴 목록(이름·설명)만으로 만든다. 툴 응답, ground truth, injection GOAL은 입력으로 쓰지 않는다. 정의는 [`src/agentgate/gate/task_spec.py`](../src/agentgate/gate/task_spec.py).

| 필드 | 형 | 의미 |
|---|---|---|
| `requested_ops` | list[str] | 프롬프트가 요구하는 상태 변경·외부 전송 툴 |
| `named_sources` | list[str] | 사용자가 지목한 자료(파일명, URL, 발신자, 채널, 일정 제목, 날짜, 도시, `tool:<이름>`) |
| `allowed_dest` | list[str] | 프롬프트에 적힌 목적지(이메일, IBAN, URL, 사용자 이름, 채널) |
| `amount_cap` | float \| null | 1회 송금 상한 |
| `egress_requested` | bool | 사용자 데이터에서 파생된 내용을 누군가에게 보내 달라는 요청인지 |
| `delegated_sources` | list[str] | (v3) 사용자가 지시 수행을 위임한 자료("이 메일에 적힌 일을 해 줘"). 읽기·요약만 요청한 자료는 해당하지 않음 |
| `notes` | str (선택) | 판단 근거. 애매하면 `[ambiguous]`로 시작 |

## 4. 판정 결합과 ESCALATE

- 한 호출에 여러 정책이 걸리면 BLOCK > ESCALATE > ALLOW 중 가장 강한 판정이 최종이다(`gate/engine.py`의 `combine_verdicts`).
- ESCALATE 처리는 실험 조건(`src/agentgate/conditions.py`)이 정한다: `block`(C1) | `allow`(C2) | `judge`(C4–C7n). 조건 없이 만든 런타임은 `runtime.escalate_action`(기본 `block`)을 쓴다.
- `judge`는 판정 LLM의 답을 B → BLOCK, A → ALLOW(되돌릴 수 있는 툴만), 그 밖은 HUMAN으로 바꾼다. 벤치마크에서 HUMAN은 BLOCK으로 집행하고 따로 센다.

## 5. v2·v3 정책

v2·v3는 v1의 P1–P9 클래스를 그대로 쓰고 아래 정책을 더한다. 버전마다 설정 파일이 따로 있다(`config/v2/policies.yaml`, `config/v3/policies_v3.yaml`, `config/v3/policies_v31.yaml`). 정책 순서는 각 버전 패키지의 `POLICY_CLASSES`가 정한다.

| 정책 | 버전 | 클래스 | 규칙 → 판정 |
|---|---|---|---|
| P0 사용자 고정 설정 | v2, v3 | `UserFixedPolicy` ([`gate/user_policy.py`](../src/agentgate/gate/user_policy.py)) | `always_block` 툴 → BLOCK, `always_human` 툴(`delete_file`, `update_password`) → ESCALATE(force_human, 판정 없이 HUMAN) |
| P10 판별기 라벨 | v2 | `DetectorLabelPolicy` ([`v2/policies.py`](../src/agentgate/v2/policies.py)) | 판별기가 flag한 응답에서 온 값 → ESCALATE, 지목하지 않은 응답의 의심 조각 안 값 → BLOCK |
| P11 툴 콘텐츠가 시킨 행동 | v3 | `InstructedActionPolicy` ([`v3/policies.py`](../src/agentgate/v3/policies.py)) | 호출이 ToolSpec 속 요청과 일치하고, 사용자가 요청하지도 위임하지도 않은 행동 → BLOCK, 그 밖 → ESCALATE. 사용자가 준 값은 일치 근거에서 뺀다 |
| P12 요청된 행동만 | v3 | `RequestedActionPolicy` | `requested_ops`에 없는 상태 변경·외부 전송(URL 방문 제외) → ESCALATE |

v3는 P7의 공격 문구 목록을 비우고 P10을 쓰지 않는다. v2의 규칙 ④(`v2/runtime.py`): 판정 LLM이 ALLOW해도, 되돌릴 수 없는 툴이고 관련 출처에 판별기 경고가 있으면 HUMAN이다.

v3.1은 v1 클래스를 고치지 않고 서브클래스로 바꾼다(`v3/policies.py`의 `*V31`).

| 표시 | 대상 | 변경 |
|---|---|---|
| A1 | P6·P8·P9 | 요청되지 않은 행동이라도 위임 자료의 요청과 일치하면 BLOCK 대신 ESCALATE |
| A2 | 규칙 J1 | 위임 자료에서 와야 하는 것은 요청 자체이고, 수행에 필요한 내용은 다른 자료에서 와도 됨 |
| A3 | 판정 입력 | 위임 자료의 ToolSpec을 항상 넣음 |
| B1 | P11 | 지목 자료 안의 링크를 여는 호출은 BLOCK 대신 ESCALATE |
| B2 | 지목 자료 일치 | 이름 비교 최소 길이 4 → 3 (`runtime.named_match_min_len`) |
| C1 | P3 | 전송을 요청했고 수신자가 신뢰 출처나 지목 자료에서 왔으면 BLOCK 대신 ESCALATE |
| D2 | P0 | `always_human` 툴이라도 `requested_ops`에 있으면 사람 확인 생략 |

판정 LLM v3는 정책 문서(`config/v3/policy_rules_v3.yaml`, v3.1은 `policy_rules_v31.yaml`)의 규칙 인덱스 전체를 system에, 트리거(`policy:<id>`, `tool_class:<분류>`, `signal:<신호>`)로 고른 조항만 user에 받는다. 정책 문서의 근거는 ATR test-corpora 공격 사례 분류(`data/v3/atr/`)이고 AgentDojo 공격 문구는 쓰지 않았다. 판정 LLM은 툴 응답 원문을 보지 않는다.
