# 설계 결정

## 1. 가로채기 지점: `FunctionsRuntime` 서브클래스 (`src/agentgate/runtime.py: GatedRuntime`)

`docs/agentdojo_api_notes.md` h절의 세 후보 중 `run_function`을 재정의하는 런타임 서브클래스를 골랐다.

- **경로 완전성**: AgentDojo에서 툴을 실행하는 코드는 `ToolsExecutor.query`(LLM 에이전트)와 `GroundTruthPipeline.query`(정답 재생) 두 곳이며, 둘 다 `runtime.run_function`만 호출한다. 중첩 `FunctionCall` 인자도 `_execute_nested_calls` → `self.run_function`으로 실행된다. 따라서 이 한 메서드가 재생 평가(Stage 5)와 파이프라인 평가(Stage 6)를 같은 코드로 덮는다.
- **주입 경로**: `TaskSuite.run_task_with_pipeline(runtime_class=...)`가 공식 인자다. `GatedRuntime.factory(**kw)`가 `runtime_class(tools)` 형태로 호출 가능한 생성기를 돌려준다.
- **출처 색인**: 원본 결과 객체와 모델에 넘어가는 문자열(`tool_result_to_str`) 양쪽을 같은 자리에서 볼 수 있다.
- **우회 없음의 증명**: `tests/test_runtime.py::test_no_tool_runs_outside_the_gate`가 `Function.__call__`을 감시해 4개 suite에서 게이트 밖 툴 실행이 0건임을 확인한다. Stage 6의 `scripts/check_enforcement.py`는 실행 기록에서 같은 사실(툴 결과와 게이트 판정의 1:1 대응)을 다시 확인한다.
- **차단 방식**: enforce 모드에서 BLOCK이면 원래 `run_function`을 부르지 않는다. 환경은 손대지 않는다. 결과로 `"Tool call blocked by security policy P<n>: <reason>"`를 돌려준다. `raise_on_error=False`(LLM 경로)에서는 같은 문자열을 오류 필드에도 넣어 모델이 실패로 인식하게 하고, `raise_on_error=True`(정답 재생)에서는 예외 없이 결과 문자열로만 돌려준다.
- **TaskSpec 미설정 시**: 런타임에 TaskSpec이 없으면 모든 호출을 BLOCK한다(fail closed).
- **Stage 6 TaskSpec 단계**: `TaskSpecElement`를 `InitQuery` 다음에 끼워 첫 LLM 호출 전에 사용자 프롬프트만으로 TaskSpec을 만들고 런타임을 설정한다. 런타임 객체가 파이프라인 요소 사이로 전달되므로 agentdojo 소스를 고치지 않고 구현된다.

## 2. 정책 해석

사양만으로 모호했던 부분과 선택한 해석이다.

1. **출처의 우선순위**: 같은 값이 여러 곳에 나오면 가장 신뢰도 높은 범주로 판정한다(PROMPT > ALLOWED_DEST > SELF > TRUSTED_TOOL > NAMED_SOURCE > OTHER_TOOL > UNSEEN). 근거 step은 그 범주의 첫 등장이다. 사양의 "최초 출처"를 문자 그대로 적용하면, 공격자가 같은 값을 더 이른 툴 응답에 먼저 심어 사용자가 지목한 출처의 정당한 값을 OTHER_TOOL로 떨어뜨릴 수 있어서다(반대 방향의 위험, 즉 공격 값이 지목 출처에도 있으면 NAMED_SOURCE가 되는 문제는 ESCALATE로 남는다).
2. **추가 출처 범주**: ALLOWED_DEST(spec.allowed_dest에 있음), SELF(설정의 사용자 자신 주소), TRUSTED_TOOL(연락처·계정 정보 등 `config/named_source_rules.yaml: trusted_tools`)을 PROMPT와 같은 신뢰로 취급한다. TRUSTED_TOOL 목록은 canary 주입으로 인젝션 벡터를 담지 않음을 검사한다(`tests/test_config_consistency.py`). slack `get_channels`는 채널 이름이 벡터라서 제외했다.
3. **지목 출처 판정**: 툴 응답이 지목 출처인지는 `named_source_rules.yaml`의 툴별 인자 값과 `spec.named_sources` 항목이 토큰 경계에서 서로 포함되는지로 정한다. 포함되는 쪽은 4자 이상이어야 한다(`query="x"`가 `bill.txt`와 맞지 않도록). `tool:<이름>` 항목은 그 툴의 모든 응답을 지목 출처로 본다.
4. **값 매칭**: 정규화(Stage 1 규칙) 후 부분 문자열 포함으로 찾는다. 단어형 값은 토큰 경계를 요구하고, 3자 미만 값(예: 파일 ID `11`)은 구조화 응답의 필드 값 또는 dict 키와 정확히 일치할 때만 등장으로 본다.
5. **P1 대상은 외부 목적지만**: slack `send_direct_message.recipient`, `send_channel_message.channel`(워크스페이스 내부 사용자·채널)은 P1 대상에서 뺐다. 내부 메시지의 위험은 내용 전파(P7)로 다룬다.
6. **P2 겹침**: 정규화 텍스트의 길이 16 k-gram 지문 교집합(= 16자 이상 공통 부분 문자열)으로 계산한다. 사용자가 프롬프트에 직접 쓴 텍스트의 k-gram은 빼고 계산한다(사용자가 준 정보는 읽기 결과의 유출이 아니다). 짧아도 결정적인 값(6자리 이상 숫자, IBAN, 카드 번호, 여권 번호 형태)은 민감 읽기 결과에 있고 프롬프트에 없으면 한 번 일치로 충분하다. 파일 첨부·공유는 egress 미요청이면 겹침과 무관하게 BLOCK이다.
7. **P3 개수**: 매칭된 읽기 응답 수와, 목록형 응답(메일 목록 등) 안에서 매칭된 개별 레코드 수 중 큰 값을 쓰고 첨부 파일 수를 더한다. P3는 egress 요청 여부와 무관하다(사양대로).
8. **P4**: amount_cap이 null이고 named_sources도 없으면, 프롬프트에 없는 금액은 ESCALATE로 처리했다(사양에 없던 경우). 반복 송금의 "신규 수신자"는 출처가 PROMPT·ALLOWED_DEST·SELF가 아닌 수신자로 정의했다. `update_scheduled_transaction`은 세션 누적·반복 판정에서 뺐다(이체 실행이 아니라 예약 변경).
9. **P5**: "프롬프트 출처가 아닌 파일 삭제"는 지목 출처에서 고른 파일도 ESCALATE다(사양 문구 그대로).
10. **P7 URL 방문**: get_webpage.url의 출처가 UNSEEN이어도 OTHER_TOOL과 같이 ESCALATE로 처리했다. 프롬프트 출처는 ALLOW.
11. **판정 LLM 결과 매핑**: 사양의 "C 또는 irreversible 툴 → HUMAN"을 "B는 항상 BLOCK, A는 가역 툴에서만 ALLOW, 그 외는 HUMAN"으로 해석했다. 비가역 툴(송금·외부 전송 등)에서 A가 나와도 사람이 확인한다.
12. **ESCALATE 판정과 enforce 재생**: Stage 5 enforce 재생은 `escalate_action=block`(설정 기본값)으로 실행했다.

## 3. 측정 관련 결정

- **executed-only security**: AgentDojo의 `functions_stack_trace`는 요청된 호출 목록이라, trace 기반으로 판정하는 slack injection_task_5는 차단된 시도도 성공으로 센다. 게이트의 효과를 보기 위해 실행된 호출만 넘긴 판정을 함께 보고한다. 일부 `security()`가 환경을 바꾸므로(메일 읽음 처리) 이 판정은 판정 전에 떠 둔 환경 사본에서 AgentDojo와 같은 순서(utility → security)로 다시 한다.
- **참조 공격 궤적**: AgentDojo가 정답을 제공하지 않는 8개 태스크는 직접 작성했다. 생성기는 현재 환경의 사본에서 계획을 세운다(`Inbox.get_unread()`의 부작용 회피).
- **Ablation 계산**: audit 재생에서는 판정이 실행을 바꾸지 않으므로 정책별 판정을 기록해 두고 사후에 결합에서 빼는 방식으로 계산했다. `--disable` 옵션으로 실제 재실행도 가능하다.

## 4. LLM 모델과 호출 설정

- **모델**: TaskSpec 추출기·판정기와 평가 대상 에이전트 모두 기본값을 `claude-opus-5-5`로 둔다(2026-10 기준 현행 Opus, 입력 $4 / 출력 $20 per 1M tokens). 환경 변수 `AGENTGATE_LLM_MODEL`, `AGENTGATE_AGENT_MODEL`로 바꿀 수 있다. 더 저렴한 대안은 `claude-sonnet-5-5`($2 / $10)이며, 바꿀지는 사용자가 정한다.
- **temperature 미사용**: 이 모델(및 현행 Claude 모델 대부분)은 `temperature`를 400으로 거부하고 thinking이 항상 켜져 있다. 그래서 temperature를 보내지 않고 `output_config.effort`(기본 `medium`)로 깊이를 정한다. 재현성은 temperature 0이 아니라 디스크 캐시(`data/specs/llm_cache/`, `data/judge_cache/`)로 확보한다. 지시서의 "temperature 0"은 이 모델에서는 적용할 수 없다.
- **에이전트 요소**: AgentDojo 0.1.35의 `AnthropicLLM`은 temperature를 항상 보내고 `max_tokens=1024`이며 assistant 턴을 [thinking/text…, tool_use…] 순서로 재구성한다. 현행 모델은 tool 호출 사이에 thinking 블록을 내고, 이전 턴 블록을 바꾸면 thinking이 무효가 된다. 이를 위해 서브클래스 `ClaudeAgentLLM`(`src/agentgate/agent_llm.py`)이 원래 content 블록을 그대로 다시 보내고, 출력 한도를 16k로 두고, 토큰·거부(refusal)를 기록한다. agentdojo 소스는 수정하지 않았다.
- **거부 시 대체 모델(fallback)**: 추출기·판정기는 서버 측 refusal fallback(`fallbacks: "default"`)을 켠다(`AGENTGATE_LLM_FALLBACKS=off`로 끔). 평가 대상 에이전트는 다른 모델이 대신 답하면 ASR이 섞이므로 fallback을 쓰지 않고, 거부 횟수를 `agent_tokens.refusals`로 기록한다.

## 5. Ollama 에이전트 (`src/agentgate/ollama_llm.py`)

AgentDojo 0.1.35에는 Ollama 요소가 없어 `BasePipelineElement`를 직접 구현했다. 근거로 삼은 API 사실(Ollama 저장소 `docs/api.md`, `docs/cloud.mdx`, `ollama-python` `_types.py`):

- `POST {host}/api/chat`, `stream: false`. 클라우드는 `host=https://ollama.com`이고 `Authorization: Bearer $OLLAMA_API_KEY` 헤더를 쓴다. 모델 이름은 `GET /api/tags`가 주는 이름을 `:cloud` 접미사 없이 쓴다.
- 응답 `message.tool_calls[].function.{name, arguments}`: arguments는 JSON 객체다. 호출 ID가 없으므로 `ollama_<턴>_<순번>`을 붙인다. 문자열로 오는 모델에 대비해 JSON 파싱도 한다. 이름이 AgentDojo 규칙(`^[a-zA-Z0-9_-]{1,64}$`)에 맞지 않는 호출은 버리고 집계한다.
- 도구 결과는 호출마다 `{"role": "tool", "content", "tool_name"}` 메시지로 돌려준다. assistant 턴은 모델이 준 `content`·`thinking`·`tool_calls`를 그대로 재전송하되, 실제로 실행한 호출만 남긴다.
- 토큰은 `prompt_eval_count`·`eval_count`로 센다. `options.temperature=0`, `seed=0`을 보내 재현성을 높인다.
- TaskSpec 추출기와 판정기는 캐시 적중 시 API 클라이언트를 만들지 않는다. 그래서 Anthropic 키 없이도 기존 Sonnet TaskSpec 캐시를 그대로 쓸 수 있다. 판정기 공급자는 `AGENTGATE_JUDGE_PROVIDER`로 따로 정한다(예: `ollama`).
