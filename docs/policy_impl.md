# 정책 구현 (Stage 3)

코드: `src/agentgate/policy/` (`base.py` 기본형, `rules.py` P1–P9, `engine.py` 결합). 설정: `config/policies.yaml`. 정책 코드에는 LLM·네트워크 호출이 없다(`grep -rnE "openai|anthropic|requests|httpx|urllib|socket|agentgate\.llm" src/agentgate/policy/` 결과 없음, `tests/policy/test_engine.py`가 같은 검사를 한다).

## 공통 구조

- `Verdict`: ALLOW / ESCALATE / BLOCK. `Decision(verdict, policy_id, reason, evidence)`. reason은 영어 한 문장, evidence는 인자·값·출처·근거 step·근거 툴 등.
- `Policy.applies_to(tool)`, `Policy.evaluate(call, ctx) -> Decision | None`. `ctx`는 `Context(spec, index, session, config)`이며 `index`가 trace의 출처 색인(`ValueIndex`)이다.
- `PolicyEngine.decide(call, ctx) -> EngineResult(verdict, decisions)`: 적용 가능한 정책을 모두 평가하고 BLOCK > ESCALATE > ALLOW로 결합한다. 걸린 모든 Decision을 함께 돌려준다. `PolicyEngine(disabled={"P3"})` 또는 `config.enabled`로 정책별 비활성화(ablation).
- 원래 None을 돌려주던 일부 경우(P7 링크 출처 확인, P8 대상 출처 확인)도 출처 근거를 남기기 위해 ALLOW Decision을 돌려준다. 결합 결과는 같다.

## 정책별

| 정책 | 대상 툴·인자 (실제 이름) | 판정 로직 요약 | 설정 키 | 사양과 다른 점 |
|---|---|---|---|---|
| P1 | `P1.targets`: send_email.recipients/cc/bcc, share_file.email, create_calendar_event.participants, add_calendar_event_participants.participants, send_money/schedule_transaction/update_scheduled_transaction.recipient, invite_user_to_slack.user_email, post_webpage.url | 원소별 `index.origin_of(v)`: PROMPT·ALLOWED_DEST·SELF·TRUSTED_TOOL→ALLOW, NAMED_SOURCE→ESCALATE, OTHER_TOOL·UNSEEN→BLOCK. 원소 중 가장 엄한 판정 | `P1.targets`, `self_identifiers`, `named_source_rules.yaml` | 추가 신뢰 범주 3개(design_decisions 2-2), 내부 메시지 수신자 제외(2-5) |
| P2 | `external_sinks`의 본문·제목·메모(+첨부·공유 file_id) | egress_requested=false일 때, sink 텍스트(프롬프트 k-gram 제외)와 민감 범주 읽기 응답의 16자 이상 공통 부분 문자열, 또는 고가치 토큰 일치, 또는 파일 첨부·공유 → BLOCK | `external_sinks`, `P2.min_overlap_chars`, `P2.high_value_token_patterns`, `P2.sensitive_categories`, `sensitive_reads.yaml` | 프롬프트 텍스트 제외, 고가치 토큰 규칙(2-6) |
| P3 | P2와 같은 sink | max(매칭된 읽기 응답 수, 목록 응답 안 매칭 레코드 수) + 첨부 수 ≥ 3 → BLOCK | `P3.min_distinct_reads`, `P3.count_attachments` | 목록형 응답의 레코드 단위 계산(2-7) |
| P4 | `P4.tools`의 amount | cap 초과→BLOCK; cap 없음: 프롬프트 금액 통과, named_sources 있으면 지목 출처 금액만 통과(그 외 ESCALATE), 둘 다 없으면 ESCALATE; 세션 누적 한도 초과→BLOCK; 같은 비프롬프트 수신자 3번째 송금→BLOCK | `P4.session_cumulative_limit`, `P4.repeat_new_recipient_threshold` | cap·지목 출처 모두 없는 경우 정의, update_scheduled_transaction 누적 제외(2-8) |
| P5 | delete_email.email_id, delete_file.file_id | 세션에서 보낸 메일 ID → BLOCK; file_id 출처가 PROMPT가 아님 → ESCALATE | `P5.email_delete_tools`, `P5.file_delete_tools` | 없음 |
| P6 | update_password, update_user_info, invite_user_to_slack, remove_user_from_slack, add_user_to_channel, share_file | requested_ops에 없음 → BLOCK; update_password.password 출처가 PROMPT 아님 → BLOCK | `P6.tools`, `P6.password_args` | share_file을 권한 변경으로 포함 |
| P7 | 메시지 본문 `P7.message_tools`, get_webpage.url | 본문에 지시 문구 또는 OTHER_TOOL 출처 URL → BLOCK; 방문 URL이 OTHER_TOOL·UNSEEN → ESCALATE, NAMED_SOURCE → ALLOW | `P7.message_tools`, `P7.visit_tools`, `P7.instruction_phrases` | UNSEEN 방문도 ESCALATE(2-10) |
| P8 | reserve_hotel.hotel, reserve_car_rental.company, reserve_restaurant.restaurant | requested_ops에 없음 → BLOCK; 대상이 신뢰 출처·지목 출처에 없음 → ESCALATE | `P8.tools` | 없음 |
| P9 | create/reschedule/cancel_calendar_event, add_calendar_event_participants | requested_ops에 없음 → BLOCK; 참석자는 P1 | `P9.tools` | 없음 |

툴별 가역성 등급(`reversibility.irreversible / reversible`)은 Stage 7 판정기가 쓴다.

## 테스트 (`tests/policy/test_p1.py` ~ `test_p9.py`, 합성 trace)

`tests/conftest.py`의 `Synth`가 프롬프트·TaskSpec·가짜 툴 응답으로 trace를 만든다. 정책마다 양성 3개 이상, 음성 3개 이상이다. 지시서가 요구한 사례는 굵게 표시했다.

- P1: test_block_destination_from_unrequested_tool, test_block_unseen_destination, test_block_attacker_iban, test_block_one_bad_element_in_list, test_allow_prompt_destination, test_allow_allowed_dest, test_allow_trusted_contacts, test_allow_self, **test_escalate_bill_iban_from_named_file** (청구서 결제에서 지목 파일의 계좌 → ESCALATE)
- P2: **test_block_penny_transfer_memo_leak** (banking 0.01달러 송금 메모 유출, 금액과 무관하게 P2), test_block_email_body_with_security_code, test_block_long_copied_text, test_block_file_attachment, test_no_flag_when_egress_requested, test_no_flag_for_unrelated_content, test_no_flag_for_values_from_prompt, test_no_flag_for_non_sensitive_reads
- P3: test_block_many_emails_in_one_list_response, test_block_many_separate_reads, test_block_many_attachments, test_no_flag_single_read, test_no_flag_two_reads, test_no_flag_no_overlap
- P4: **test_allow_normal_transfer_prompt_iban_and_amount** (정상 송금, 엔진 전체 ALLOW), test_block_over_cap, test_block_session_cumulative, test_block_repeated_new_recipient, test_escalate_amount_not_in_named_source, test_allow_amount_from_named_source, test_allow_update_without_amount
- P5: **test_block_deleting_email_sent_by_agent** (에이전트가 보낸 메일 삭제 → BLOCK), test_block_deleting_any_of_several_sent, test_escalate_file_not_in_prompt, test_escalate_unseen_file, test_allow_deleting_received_email, test_allow_file_named_in_prompt, test_allow_delete_email_without_sends
- P6: test_block_password_change_not_requested, test_block_password_not_from_prompt, test_block_invite_not_requested, test_block_share_not_requested, test_allow_password_from_prompt, test_allow_requested_invite, test_allow_requested_user_info_update
- P7: test_block_link_from_unrequested_response, test_block_instruction_phrase, test_block_phishing_link_in_email, test_escalate_visit_url_from_other_tool, test_allow_visit_link_inside_named_source, test_allow_prompt_url, test_no_flag_plain_message
- P8: test_block_reservation_not_requested, test_block_car_not_requested, test_block_restaurant_not_requested, test_escalate_target_from_unnamed_source, test_allow_prompt_hotel, test_allow_hotel_from_named_city_listing, test_engine_allows_requested_prompt_reservation
- P9: test_block_create_not_requested, test_block_cancel_not_requested, test_block_reschedule_not_requested, test_allow_requested_create, test_allow_requested_reschedule, test_external_attendee_left_to_p1

## pytest 전체 결과

```
$ python -m pytest -q
128 passed
```

정책 테스트 외에 출처 판정(`tests/test_trace.py`), 래퍼·우회 경로(`tests/test_runtime.py`), 설정 사실 검증(`tests/test_config_consistency.py`), 참조 공격 궤적 35개(`tests/test_attack_trajectories.py`), LLM 구성요소 가짜 클라이언트 테스트(`tests/test_llm_components.py`), 파이프라인 통합(`tests/test_pipeline.py`)을 포함한다.
