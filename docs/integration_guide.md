# contract v1 세부 규약

외부 프로세스 Wrapper가 HTTP로 Gate를 부르는 규약(contract v1)과 우회 감지 규칙이다. 어디에 Adapter를 붙이는지, Python Adapter 작성 절차는 [`adapter_guide.md`](adapter_guide.md)에 있다.

## 실행 방식

agentgate가 Wrapper를 프로세스로 띄우고([`adapters/contract.py`](../src/agentgate/adapters/contract.py)) 환경 변수를 넘긴다. client는 표준 라이브러리만 쓰는 [`harness/agentgate_client.py`](../src/agentgate/harness/agentgate_client.py)이며 Wrapper에 복사해 쓴다.

| 환경 변수 | 뜻 |
|---|---|
| `AGENTGATE_URL` | 서비스 주소(예: `http://127.0.0.1:8000`) |
| `AGENTGATE_SESSION` · `AGENTGATE_TOKEN` | session id와 bearer token |
| `AGENTGATE_MODEL` | 모델 JSON(`provider`, `model`, `base_url`, `api_key`, `litellm`, `openai`) |
| `AGENTGATE_MAX_STEPS` · `AGENTGATE_OPTIONS` | 단계 상한, 프레임워크 옵션 JSON |

## 엔드포인트와 `GateSession`

서비스는 [`harness/service.py`](../src/agentgate/harness/service.py)다. `127.0.0.1`에만 바인딩하고 session마다 bearer token을 요구한다. 대시보드가 같은 router를 mount한다.

| `GateSession` | HTTP (`/v1/sessions...`) | 용도 |
|---|---|---|
| `from_env()` | — | 위 환경 변수로 session에 붙는다 |
| `create(url, scenario, framework, model)` | `POST /v1/sessions` | Wrapper가 session을 직접 연다 |
| `prompt()`, `info()` | `GET /{id}` | 사용자 프롬프트, session 정보 |
| `tools()`, `openai_tools()` | `GET /{id}/tools` | 툴 목록(JSON schema) |
| `invoke(tool, args)` | `POST /{id}/invoke` | 툴 실행 대신 부른다. 에이전트에게 줄 문자열(차단 메시지 포함) |
| `invoke_detail(tool, args)` | `POST /{id}/invoke` | `{output, blocked, verdict, effective, policy_id, reason}` |
| `decide(tool, args)` | `POST /{id}/decide` | Wrapper 자체 툴의 판정만. AgentDojo 툴이면 400 |
| `event(kind, message, step, data)` | `POST /{id}/events` | 경계 로그(선택, 실패해도 무시) |
| `finish(final_output)` | `POST /{id}/finish` | 최종 답을 보내고 채점 결과를 받는다 |
| `model_from_env()`, `max_steps_from_env()` | — | 모델·단계 상한 |

Wrapper는 `gate_decision`·`gate_bypass`·`result` 이벤트를 보낼 수 없다(서비스가 무시한다).

## 차단 메시지

에이전트는 아래 문자열을 툴 결과로 받는다. 형식을 바꾸지 않는다.

- 판정기가 막음: `Tool call blocked by policy gate (R2, R3): <이유>` (이유 240자 이내)
- TaskSpec 없음: `Tool call blocked by policy gate: no TaskSpec is configured for this task (fail closed).`
- Gate에 닿지 못함(client): `Tool call blocked by security policy GATE: agentgate is unreachable (<오류>); the call was not executed.`

## 우회 감지

`GateToolBridge.ungated_calls`([`harness/bridge.py`](../src/agentgate/harness/bridge.py))는 이벤트 `tool_start`의 `data["action"]`과 Gate가 판정한 호출을 대조한다. suite 툴이 Gate가 본 횟수보다 많이 시작되었거나, suite 툴도 `internal_tools`도 아닌 툴이 시작되면 run은 무효다.

- 프레임워크가 `[TOOL_EXECUTION_START] action=%s ...` 형식 로그를 남기면 Adapter의 `loggers`에 logger 이름만 적는다([`harness/events.py`](../src/agentgate/harness/events.py)의 `BoundaryLogHandler`).
- 그렇지 않으면 툴 실행 지점에서 `tool_start` → `invoke` → `tool_end`를 직접 남긴다(Adapter는 `sink.emit`, Wrapper는 `gate.event`). 실행 지점 한 곳에서 한 번씩만 남긴다.

## 등록과 검사

```yaml
# config/frameworks.yaml
frameworks:
  my-wrapper:
    contract:
      command: [python, path/to/wrapper.py]
      cwd: path/to/wrapper/repo        # 선택
      timeout: 1800                    # 선택, 초
      internal_tools: [final_answer]   # 선택
```

```bash
python -m agentgate.harness.conformance --framework my-wrapper --conditions no_gate gate_static_rules
python -m agentgate.harness.conformance --entry "python examples/contract_wrapper/minimal_agent.py"   # 등록 전 검사
```
