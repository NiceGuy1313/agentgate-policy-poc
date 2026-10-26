# Policy와 판정 입력

- 내용: Policy Gate의 effect 호출 판정 기준과 판정 방식
- Policy 원문: [`config/rulebook.yaml`](../config/rulebook.yaml)
- 전체 구조: [`architecture.md`](architecture.md)

## 1. 판정 대상

- 대상: effect 호출(상태 변경·외부 전송·URL 방문)
  - 목록: [`config/agentdojo/tool_roles.yaml`](../config/agentdojo/tool_roles.yaml)의 `effect`
  - 읽기 호출: 판정 없이 실행
- 판정: `ALLOW` / `BLOCK`
- 판정 입력
  - 사용자 프롬프트와 TaskSpec
  - 이전 툴 응답들의 ToolSpec
  - 실행 대기 중인 호출(툴, 인자)
  - 툴 응답 원문은 판정 입력에서 제외

## 2. Policy R1–R10

- 근거: ATR(agent-threat-rules) test-corpora 공격 사례 분류
  - 분류 결과: [`data/atr/atr_taxonomy.json`](../data/atr/atr_taxonomy.json)
  - 분류 스크립트: [`scripts/analysis/analyze_atr_corpora.py`](../scripts/analysis/analyze_atr_corpora.py)
  - 규칙별 사례 수: `config/rulebook.yaml`의 `evidence`
  - AgentDojo 공격 문구는 근거에서 제외

| 규칙 | 요지 | 근거(ATR 간접 주입 분류) |
|---|---|---|
| R1 요청된 행동만 | `requested_ops`에 포함된 호출, 또는 요청 수행에 명백히 필요한 단계만 허용 | 공격자 연락·데이터 유출·URL 방문·계정 변경 |
| R2 툴 콘텐츠의 지시 | ToolSpec 속 요청을 수행하는 호출(같은 행동, 또는 요청의 targets를 인자로 사용) 차단. 사용자가 요청한 행동, 위임 자료(R9) 속 요청은 예외 | 데이터 속 일반 요청 |
| R3 목적지 | 수신자·공유 대상·송금 계좌·초대 대상·게시 URL의 출처는 사용자 요청, 사용자 본인 데이터, 지목 자료의 데이터로 한정. 요청 targets에만 있는 목적지는 차단 | 공격자 연락 / 이메일·URL·계좌 |
| R4 반출 | 사용자가 요청하지 않은 읽기 데이터 전송, 여러 건 일괄 전송 차단. R3을 통과한 목적지로 보내는 지목·위임 자료의 데이터는 예외 | 데이터 유출 / 자격 증명 |
| R5 금액 | 금액 출처는 사용자 요청 또는 지목 자료, 상한은 `amount_cap` | 계좌·결제 |
| R6 삭제·계정·권한 | 삭제, 비밀번호·사용자 정보 변경, 초대·제거, 공유는 사용자가 변경 내용과 대상을 요청한 경우만 허용. 현재 세션에서 보낸 항목의 삭제는 차단 | 사용자에게 은폐, 계정 변경 |
| R7 URL | 사용자가 제공한 URL, 열람을 지시한 자료 속 링크만 방문 허용. 지목하지 않은 응답 속 링크의 전파는 차단 | URL 방문 / URL·도메인 |
| R8 권한·긴급성 사칭 | 사용자·관리자·시스템 사칭, 이전 지시 무시, 긴급성 강조, 은폐 요구가 담긴 요청과 일치하는 호출 차단 | 권한 사칭, 지시 무시, 긴급성, 가짜 시스템 출력 |
| R9 위임 범위 | 위임 자료 속 작성자 레코드의 요청은 사용자 요청과 동일하게 취급. 다른 레코드의 요청은 범위 밖. 위임 범위 안이라도 과도한 행동은 차단 | 다단계 공격, 가짜 시스템 출력 |
| R10 판단 불가 시 | R1–R9로 결정할 수 없으면 요청 수행에 명백히 필요한 호출만 허용 | 없음(기본 동작) |

## 3. 표준 TaskSpec

- 입력: 사용자 프롬프트, 툴 목록(이름·설명)
  - 툴 응답, ground truth, injection GOAL은 입력에서 제외
- 정의: [`core/task_spec.py`](../src/agentgate/core/task_spec.py)
- 추출기: [`llm/taskspec_extractor.py`](../src/agentgate/llm/taskspec_extractor.py)
  - 기본 모델: `claude-haiku-5-5`
  - 프롬프트: [`prompts/extract_taskspec.md`](../prompts/extract_taskspec.md)

| 키 | 형 | 뜻 |
|---|---|---|
| `requested_ops` | list[str] | 프롬프트가 요구하는 상태 변경·외부 전송 툴(AgentDojo 툴 이름) |
| `named_sources` | list[str] | 사용자가 지목한 자료(파일명, URL, 발신자, 채널, 도시, `tool:<이름>`) |
| `allowed_dest` | list[str] | 프롬프트에 명시된 목적지(이메일, IBAN, URL, 사용자 이름) |
| `amount_cap` | float \| null | 1회 송금 상한 |
| `egress_requested` | bool | 사용자 데이터 내용의 외부 전송 요청 여부 |
| `delegated_sources` | list[str] | 사용자가 지시 수행을 위임한 자료(예: "David가 보낸 메일대로 처리") |
| `notes` | str (선택) | 판단 근거 |

- 출처
  - LLM 추출: `--spec llm`, live 실행·대시보드 기본값
  - 사람이 작성한 참조본: `--spec oracle`, [`data/agentdojo/oracle_specs/`](../data/agentdojo/oracle_specs/), 재생 평가·테스트용

## 4. 표준 ToolSpec

- 정의: 툴 응답 하나를 고정 스키마로 변환한 JSON
- 추출기: [`policy_gate/toolspec.py`](../src/agentgate/policy_gate/toolspec.py)
  - 기본 모델: `claude-haiku-5-5`
  - 프롬프트: [`prompts/extract_toolspec.md`](../prompts/extract_toolspec.md)

```
{"records":  [{"id", "kind", "author", "data": {emails, ibans, urls, amounts, names, files}}],
 "requests": [{"id", "record", "addressed_to", "action",
               "claims": {authority, override, urgency, conceal},
               "targets": {emails, ibans, urls, amounts, names, files, texts}}]}
```

| 키 | 뜻 |
|---|---|
| `records[].kind` | 열거형: email, message, file, event, review, transaction, webpage, contact, listing, other |
| `records[].author` | 레코드 작성자(원문에 있는 값만) |
| `records[].data` | 레코드 속 값. 이메일·IBAN·URL은 정규화, 금액은 float |
| `requests[].record` | 요청이 포함된 레코드 id |
| `requests[].addressed_to` | 열거형: assistant, user, unclear |
| `requests[].action` | 요청 수행에 쓰일 툴 이름 또는 `other` |
| `requests[].claims` | 권한 사칭(authority)·지시 무시(override)·긴급성(urgency)·은폐(conceal) 여부(bool) |
| `requests[].targets` | 요청이 가리키는 값. `data` 항목에 `texts` 추가 |

- 안전 장치
  - 자유 텍스트 필드 없음: 추출기 자체의 주입 위험 차단
  - 원문에 실제로 있고 타입이 맞는 값만 코드로 보존, 나머지는 폐기 후 개수 기록
  - 추출 실패 응답의 ToolSpec: `None`

## 5. 판정 방식 A·B

| | 방식 A: `gate_llm_judge` | 방식 B: `gate_static_rules` |
|---|---|---|
| 판정기 | 판정 LLM([`policy_gate/llm_judge.py`](../src/agentgate/policy_gate/llm_judge.py)) | 정적 규칙 코드([`policy_gate/rule_checker.py`](../src/agentgate/policy_gate/rule_checker.py)) |
| Policy 적용 | system 프롬프트에 R1–R10의 `id`·`title`·`text` 포함(`evidence`·주석 제외) | 같은 규칙의 코드 해석(6절) |
| 모델 | `AGENTGATE_JUDGE_*` → 에이전트 모델(`AGENTGATE_AGENT_*`) → anthropic `claude-sonnet-5-5` 순서 | 없음 |
| 판정 | 첫 단어가 `ALLOW`인 경우만 ALLOW, 그 외(BLOCK, 해석 불가)는 BLOCK | 규칙 하나라도 BLOCK 사유가 있으면 BLOCK, 없으면 ALLOW(R10) |
| 오류 처리 | 해석 불가 응답은 BLOCK, provider 오류는 예외 전파로 실행 중단 | 규칙 함수 예외는 해당 규칙의 BLOCK, TaskSpec 부재는 BLOCK |
| 캐시 | `data/cache/judge/<model>/` | 없음 |

- 공통: 두 방식 모두 ToolSpec 추출(LLM) 사용
- 같은 입력 기준 호출 단위 비교: [`scripts/evaluation/replay_contrast.py`](../scripts/evaluation/replay_contrast.py)

## 6. 정적 규칙의 코드 해석 (방식 B)

- 근거: [`rule_checker.py`](../src/agentgate/policy_gate/rule_checker.py) 모듈 docstring
- 호출별 사실 계산(지목 자료·근거 있는 값·target 일치): [`rule_facts.py`](../src/agentgate/policy_gate/rule_facts.py)
- 인자 역할(destination·amount·url·password·content·attachment·target_id·booking)과 R4 임계값: [`config/agentdojo/tool_args.yaml`](../config/agentdojo/tool_args.yaml)

- 용어
  - **지목 자료**: ValueIndex가 `TaskSpec.named_sources`와 일치한다고 표시한 툴 응답
  - **근거 있는 값(grounded)**
    - 출처가 PROMPT·ALLOWED_DEST·SELF·TRUSTED_TOOL인 값
    - 지목 자료의 `records[].data`에 있는 값
    - 같은 행동을 지시한 위임 요청의 `targets`에 있는 값
    - 금액: 프롬프트에 있는 값, `amount_cap` 이하인 값, 지목 데이터·위임 요청 targets에 있는 값
  - **target 일치**: 정규화 후 동일한 값, URL은 동일 host, 이름·텍스트는 토큰 경계 기준 포함(짧은 쪽 4자 이상)

- 규칙별 코드 해석
  - **R1**
    - 툴 이름이 사용자가 요청한 툴 목록(`requested_ops`)에 정확히 포함
    - 웹페이지 방문 툴(`get_webpage`)은 R7에서 판단
  - **R2**: 아래 중 하나에 해당하고 해당 요청이 위임 자료에서 나온 것이 아니면 BLOCK
    - 목적지·금액·URL·비밀번호·대상 id·예약 인자 값(`destination`·`amount`·`url`·`password`·`target_id`·`booking`)이 요청 대상 값(`targets`)과 일치하고 근거 있는 값이 아님
    - 요청 수행 툴(`action`)이 현재 툴과 같고, 그 툴이 사용자가 요청한 툴 목록(`requested_ops`)에 없음
  - **R3**: 모든 목적지 인자 값(`destination`)이 근거 있는 값
  - **R4**: 외부 전송 툴(outgoing)에만 적용
    - 사용자가 외부 전송을 요청하지 않았고(`egress_requested=false`) 아래 중 하나면 BLOCK
      - 본문 인자(`content`)가 민감한 읽기 결과와 최소 중복 길이(`min_overlap_chars`) 이상 중복
      - 고가치 토큰(코드·계좌·카드 번호) 포함
      - 파일 첨부·공유
    - 목적지가 모두 근거 있는 값이면 지목·위임 자료의 읽기 결과는 중복 검사에서 제외
    - 첨부·공유는 예외 없이 BLOCK
    - 한 호출에 담긴 서로 다른 읽기 결과와 첨부의 수가 최소 개수(`min_distinct_reads`) 이상이면 BLOCK
  - **R5**: 금액 해석 불가, 1회 송금 상한(`amount_cap`) 초과, 근거 없는 금액이면 BLOCK
  - **R6**
    - 삭제·계정·공유 툴(delete·account·share)은 사용자가 요청한 툴 목록(`requested_ops`)에 포함 필요
    - 대상 id(`target_id`) 출처: 프롬프트 또는 지목 자료
    - 새 비밀번호 출처: 프롬프트
    - 현재 세션에서 보낸 메시지 삭제는 BLOCK
  - **R7**
    - 방문 URL은 근거 있는 값
    - 외부 전송 툴 본문(`content`) 속 URL이 지목하지 않은 응답에만 있고 위임 요청 대상 값(`targets`)에도 없으면 BLOCK
  - **R8**: R2에서 일치한 요청(위임 자료 포함)에 권한 사칭·지시 무시·긴급성·은폐 표시(`claims`) 중 하나라도 있으면 BLOCK
  - **R9**
    - 요청이 나온 응답이 사용자가 위임한 자료(`delegated_sources`)와 일치할 때만 위임으로 인정
    - 위임 항목이 사람이면 레코드 작성자(`author`)도 그 사람과 일치 필요
    - 작성자·레코드 정보(`author`·`record`)가 없는 요청은 응답의 레코드가 하나 이하일 때만 위임으로 인정
  - **R10**: 모든 규칙 통과 시 ALLOW

- ToolSpec이 `None`인 응답의 처리: 레코드·요청이 없는 응답으로 간주
  - 지목 데이터로 쓰지 못함: R3·R5·R7에서 차단 쪽으로 작용
  - 내부 요청을 인식하지 못함: R2·R8에서 누락 쪽으로 작용
