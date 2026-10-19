# 에이전트 프레임워크 연결 가이드

새 에이전트 프레임워크를 AgentGate에 붙이는 팀원을 위한 문서다. 목표는 하나다. **그 프레임워크가 실행하는 AgentDojo 툴 호출이 모두 Policy Gate를 지나게 한다.** 구조 전체는 [`architecture.md`](architecture.md), 판정 규칙은 [`rulebook.md`](rulebook.md)에 있다.

## 목차

1. [Wrapper와 Adapter](#1-wrapper와-adapter)
2. [연결 방식 고르기](#2-연결-방식-고르기)
3. [Python Adapter로 연결하기](#3-python-adapter로-연결하기)
4. [외부 프로세스 Wrapper로 연결하기 (contract v1)](#4-외부-프로세스-wrapper로-연결하기-contract-v1)
5. [반드시 지킬 규칙](#5-반드시-지킬-규칙)
6. [체크리스트](#6-체크리스트)
7. [자주 하는 실수](#7-자주-하는-실수)

## 1. Wrapper와 Adapter

| 구분 | 하는 일 | 차단 가능 여부 |
|---|---|---|
| 추적용 Wrapper | 프레임워크의 LLM 호출·툴 실행을 로그로 관측한다(NiceGuy1313 fork의 경계 로그 `[TOOL_EXECUTION_START]` 등) | 불가. 툴은 이미 프레임워크 안에서 실행된다 |
| Adapter | 프레임워크에 툴을 등록할 때 **툴 함수 본문을 `bridge.invoke(name, args)`로 바꾼다** | 가능. 실행 여부를 Gate가 정한다 |

추적만 하는 Wrapper로는 호출을 막을 수 없다. 로그가 남는 시점에 툴은 이미 실행되었기 때문이다. 차단하려면 프레임워크가 툴을 실행하는 자리에서 실제 툴 대신 Gate를 불러야 한다. 이 저장소에서 "Wrapper"는 이 일을 HTTP로 하는 외부 프로세스(contract v1 Wrapper, 4절)를 뜻하고, 같은 일을 같은 프로세스 안에서 하는 것이 Adapter(3절)다.

경계 로그는 차단에는 쓰지 않고 **우회 감지**에만 쓴다. 프레임워크가 `tool_start`를 남겼는데 Gate 판정이 없으면 그 run은 무효다(3절 ③).

## 2. 연결 방식 고르기

| 상황 | 방식 | 참조 구현 |
|---|---|---|
| 프레임워크가 Python이고 agentgate와 같은 venv에 설치된다 | Python Adapter | [`smolagents_adapter.py`](../src/agentgate/adapters/smolagents_adapter.py) |
| Python이지만 의존성 버전이 agentgate와 충돌한다 | Python Adapter + `frameworks.yaml`의 `python:`(별도 venv worker) | [`browser_use_adapter.py`](../src/agentgate/adapters/browser_use_adapter.py) |
| 다른 언어이거나 별도 저장소의 독립 프로그램이다 | 외부 프로세스 Wrapper(contract v1) | [`examples/contract_wrapper/minimal_agent.py`](../examples/contract_wrapper/minimal_agent.py) |

## 3. Python Adapter로 연결하기

### ① `FrameworkAdapter` 서브클래스 작성

규약은 [`adapters/base.py`](../src/agentgate/adapters/base.py)에 있다.

| 항목 | 뜻 |
|---|---|
| `name`, `display_name`, `description` | 등록 이름과 대시보드 표시 |
| `requires` | import해야 하는 모듈. 없으면 대시보드에서 비활성 |
| `package` | 실행 manifest에 남길 배포 이름(버전 기록) |
| `loggers` | 경계 로그를 받을 logger 이름(③) |
| `internal_tools` | AgentDojo 툴이 아닌 프레임워크 자체 툴(예: `final_answer`, `done`) |
| `routes_tools_through_bridge` | **반드시 `True`**. 없으면 registry와 runner가 실행을 거부한다(`contract_problems`) |
| `default_max_steps` | 기본 15(AgentDojo 툴 루프 상한과 맞춤) |
| `run(task, bridge, model, options, max_steps, sink)` | 에이전트 루프를 한 번 돌리고 `FrameworkResult`를 돌려준다 |

`run`의 인자:
- `task`: 사용자 프롬프트(str).
- `bridge`: `GateToolBridge`([`harness/bridge.py`](../src/agentgate/harness/bridge.py)). `tool_specs()`는 `[{name, description, parameters(JSON schema)}]`, `invoke(tool, args)`는 에이전트에게 돌려줄 문자열, `invoke_detail(tool, args)`는 `(문자열, 판정 기록)`을 돌려준다.
- `model`: `ModelSpec`([`harness/models.py`](../src/agentgate/harness/models.py)). `litellm_kwargs()` 또는 `openai_kwargs()`로 프레임워크의 모델 인자를 만든다. 제공자를 코드에 고정하지 않는다.
- `options`: `options_schema()`에 선언한 프레임워크 옵션(dict).
- `sink`: `EventSink`. `sink.emit(kind, message, level=..., step=..., data=...)`로 단계 이벤트를 남긴다.

반환값 `FrameworkResult(final_output, steps=0, usage={"input", "output", "calls"}, error=None, messages=None, info={})`.

핵심은 툴 등록이다. 툴마다 프레임워크의 툴 객체를 만들고, 그 실행 함수의 본문을 `bridge.invoke(name, args)` 한 줄로 둔다.

```python
from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.harness.tool_schema import arguments


class MyFrameworkAdapter(FrameworkAdapter):
    name = "my-framework"
    display_name = "My framework"
    requires = ("my_framework",)
    package = "my-framework"
    loggers = ("my_framework",)                 # 경계 로그 logger (③)
    internal_tools = frozenset({"final_answer"})
    routes_tools_through_bridge = True          # 의무 Gate 경로 선언

    def run(self, task, bridge, model, options, max_steps, sink):
        import my_framework

        tools = []
        for spec in bridge.tool_specs():
            params = {a.name: a.description for a in arguments(spec["parameters"])}

            def call(_name=spec["name"], **kwargs):
                args = {k: v for k, v in kwargs.items() if v is not None}
                return bridge.invoke(_name, args)   # 툴 본문: 실행 대신 Gate 호출

            tools.append(my_framework.Tool(name=spec["name"], description=spec["description"],
                                           parameters=params, fn=call))

        agent = my_framework.Agent(tools=tools, max_steps=max_steps, **model.litellm_kwargs())
        output = agent.run(task)
        return FrameworkResult(final_output=None if output is None else str(output), steps=agent.steps)
```

(`my_framework`의 API는 예시다. 실제 등록 방식은 [`smolagents_adapter.py`](../src/agentgate/adapters/smolagents_adapter.py)의 `make_tools`를 따른다.)

### ② `config/frameworks.yaml` 등록

```yaml
frameworks:
  my-framework:
    adapter: my_package.my_adapter:MyFrameworkAdapter
    python: .venv-my-framework/bin/python    # 선택: 의존성이 충돌할 때만, 별도 venv worker에서 실행
    display_name: My framework                # 선택
```

`agentgate.frameworks` entry point로 등록해도 된다([`harness/registry.py`](../src/agentgate/harness/registry.py)). 등록하면 대시보드, 시나리오 CLI, conformance 검사에 나타나며 서버·UI 코드는 고치지 않는다.

### ③ 경계 로그 연결(우회 감지)

`GateToolBridge.ungated_calls`는 이벤트 `tool_start`의 `data["action"]`과 Gate가 판정한 호출을 대조한다.
- suite 툴이 Gate가 본 횟수보다 많이 시작되었거나, suite 툴도 `internal_tools`도 아닌 툴이 시작되면 우회로 본다.
- 하나라도 있으면 run은 무효(`valid: false`)이고 점수는 집계에서 빠진다.

연결 방법은 둘 중 하나다.
- 프레임워크가 `"[TOOL_EXECUTION_START] action=%s params=%s"`, `"[TOOL_EXECUTION_END] action=%s ..."` 형식의 로그를 남기면 `loggers`에 logger 이름만 적는다. `BoundaryLogHandler`가 format 문자열의 `key=%s`를 읽어 이벤트로 바꾼다([`harness/events.py`](../src/agentgate/harness/events.py)).
- 그런 로그가 없으면 툴 실행 지점 앞뒤에서 직접 남긴다: `sink.emit("tool_start", name, data={"action": name})` → `bridge.invoke(...)` → `sink.emit("tool_end", name, data={"action": name})`.

이벤트 순서는 `tool_start → gate_decision → tool_end`여야 한다. `gate_decision`은 `bridge.invoke`가 남긴다.

### ④ conformance 통과

```bash
python -m agentgate.harness.conformance --framework my-framework --condition gate_static_rules
```

LLM·네트워크 없이 가짜 LLM([`harness/fake_llm.py`](../src/agentgate/harness/fake_llm.py))이 정답 궤적과 공격 궤적을 따르게 하고, native AgentDojo pipeline의 결과와 비교한다([`harness/conformance.py`](../src/agentgate/harness/conformance.py)). 통과 조건:
- `contract_problems()`가 비어 있다.
- Gate trace(툴, 최종 판정, 차단 여부)와 utility·security가 기준과 같다.
- 차단된 호출이 실행되지 않았고 환경이 바뀌지 않았다.
- Gate 밖 툴 실행이 없고 이벤트 순서가 맞다.

출력 끝의 `N/N passed`를 확인한다. 실패하면 `FAIL` 아래에 원인이 나온다. 조건을 생략하면 `no_gate`와 `gate_static_rules`를 모두 검사한다.

### ⑤ 시나리오 CLI·대시보드에서 실행

```bash
python -m agentgate.scenarios.run --framework my-framework --condition gate_static_rules \
  --model ollama:gpt-oss:120b --pilot --out results/runs/my-framework
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000   # 프레임워크 목록에 나타난다
```

`--agent scripted --spec oracle`을 주면 에이전트 LLM과 TaskSpec 추출을 부르지 않는다. 다만 Gate 조건의 ToolSpec 추출은 캐시(`data/cache/toolspec/`)가 없으면 `ANTHROPIC_API_KEY`가 필요하다. 키 없이 확인하는 방법은 conformance(④)다.

## 4. 외부 프로세스 Wrapper로 연결하기 (contract v1)

Python Adapter 없이, Wrapper 프로그램이 HTTP로 Gate를 부른다. 서비스는 [`harness/service.py`](../src/agentgate/harness/service.py), client는 표준 라이브러리만 쓰는 [`harness/agentgate_client.py`](../src/agentgate/harness/agentgate_client.py)(Wrapper에 복사해 사용)다.

### 실행 방식

agentgate가 Wrapper를 프로세스로 띄우고 환경 변수를 넘긴다.

| 환경 변수 | 뜻 |
|---|---|
| `AGENTGATE_URL` | 서비스 주소(예: `http://127.0.0.1:8000`) |
| `AGENTGATE_SESSION` · `AGENTGATE_TOKEN` | session id와 bearer token |
| `AGENTGATE_MODEL` | 모델 JSON(`provider`, `model`, `base_url`, `api_key`, `litellm`, `openai`) |
| `AGENTGATE_MAX_STEPS` · `AGENTGATE_OPTIONS` | 단계 상한, 프레임워크 옵션 JSON |

### `GateSession` API

| 메서드 | 용도 |
|---|---|
| `GateSession.from_env()` | 위 환경 변수로 session에 붙는다 |
| `GateSession.create(url, scenario, framework, model)` | Wrapper가 스스로 session을 연다(`POST /v1/sessions`) |
| `prompt()`, `tools()`, `openai_tools()` | 사용자 프롬프트, AgentDojo 툴 목록(JSON schema) |
| `invoke(tool, args)` | 툴 실행 대신 부른다. 에이전트에게 줄 문자열(차단 메시지 포함)을 돌려준다 |
| `invoke_detail(tool, args)` | `{output, blocked, verdict, effective, policy_id, reason}` |
| `decide(tool, args)` | Wrapper 자체 툴의 판정만 받는다. AgentDojo 툴을 넣으면 400 |
| `event(kind, message, step, data)` | 경계 로그를 대시보드로 보낸다(선택, 실패해도 무시) |
| `finish(final_output)` | 최종 답을 보내고 채점 결과를 받는다 |
| `model_from_env()`, `max_steps_from_env()` | 모델·단계 상한 |

### 골격

```python
from agentgate_client import GateSession

gate = GateSession.from_env()
model, max_steps = GateSession.model_from_env(), GateSession.max_steps_from_env()
tools = gate.openai_tools()                      # 에이전트 툴로 등록
messages = [{"role": "user", "content": gate.prompt()}]
final = None
for step in range(1, max_steps + 1):
    reply = call_llm(model, messages, tools)     # Wrapper 자신의 LLM 호출
    if not reply.tool_calls:
        final = reply.content
        break
    for call in reply.tool_calls:
        gate.event("tool_start", call.name, step=step, data={"action": call.name})
        output = gate.invoke(call.name, call.args)   # 실행 hook
        gate.event("tool_end", call.name, step=step, data={"action": call.name})
        messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
gate.finish(final)
```

전체 예시는 [`minimal_agent.py`](../examples/contract_wrapper/minimal_agent.py)다.

### 등록과 검사

```yaml
frameworks:
  my-wrapper:
    contract:
      command: [python, path/to/wrapper.py]
      cwd: path/to/wrapper/repo        # 선택
      timeout: 1800                    # 선택, 초
      internal_tools: [final_answer]   # 선택
```

```bash
python -m agentgate.harness.conformance --framework my-wrapper --condition gate_static_rules
python -m agentgate.harness.conformance --entry "python path/to/wrapper.py"   # 등록 전 검사
```

## 5. 반드시 지킬 규칙

1. **툴을 직접 실행하지 않는다.** AgentDojo 툴 함수, `suite.tools`, 환경 객체를 Adapter·Wrapper에서 부르지 않는다. `GatedRuntime`이 감싼 툴 함수는 `run_function` 밖에서 부르면 실행되지 않고 `UngatedExecutionError`가 나며, run은 무효가 된다.
2. **Gate 응답 문자열을 그대로 에이전트에 돌려준다.** 차단 메시지도 정상 툴 결과처럼 돌려준다. 예외로 바꾸거나, 고쳐 쓰거나, 숨기거나, 같은 호출을 자동으로 재시도하지 않는다.
3. **차단 메시지 형식을 바꾸지 않는다.** 에이전트가 받는 형식은 다음과 같다.
   - 판정기가 막음: `Tool call blocked by policy gate (R2, R3): <이유>` (이유 240자 이내)
   - TaskSpec 없음: `Tool call blocked by policy gate: no TaskSpec is configured for this task (fail closed).`
   - Gate에 닿지 못함(Wrapper client): `Tool call blocked by security policy GATE: agentgate is unreachable (<오류>); the call was not executed.`
4. **fail-closed를 유지한다.** Gate 호출이 실패하면 호출을 실행하지 않고 차단으로 처리한다. `agentgate_client.py`는 이미 그렇게 동작하므로 고치지 않는다.
5. **의무 Gate를 선언하고 검사를 통과한다.** `routes_tools_through_bridge = True`와 conformance 통과는 함께 필요하다. 선언만으로는 부족하다.
6. **Wrapper는 판정을 보고하지 않는다.** `gate_decision`·`gate_bypass`·`result` 이벤트는 agentgate만 만든다. Wrapper가 보내면 무시한다.

## 6. 체크리스트

- [ ] 툴마다 실행 함수 본문이 `bridge.invoke(name, args)`(또는 `gate.invoke`)뿐이다.
- [ ] `routes_tools_through_bridge = True`를 선언했다(Python Adapter).
- [ ] 프레임워크 자체 툴을 `internal_tools`에 적었다.
- [ ] `tool_start`/`tool_end`가 툴 실행 지점에서 한 번씩 기록된다(`loggers` 또는 `sink.emit`).
- [ ] 모델은 `ModelSpec`(또는 `AGENTGATE_MODEL`)에서 받는다. 제공자를 고정하지 않았다.
- [ ] `config/frameworks.yaml`(또는 entry point)에 등록했다.
- [ ] `python -m agentgate.harness.conformance --framework <name>`이 모두 PASS다.
- [ ] `python -m agentgate.scenarios.run --framework <name> --agent scripted --spec oracle --pilot --out <dir>`이 무효 run 없이 끝난다(ToolSpec 캐시 또는 키 필요).
- [ ] `python -m pytest -q`가 통과한다.

## 7. 자주 하는 실수

| 실수 | 결과 | 바로잡기 |
|---|---|---|
| 툴 함수 안에서 AgentDojo 함수를 직접 부름 | `UngatedExecutionError`, run 무효 | 본문을 `bridge.invoke`로 바꾼다 |
| 경계 로그만 붙이고 툴 본문은 그대로 둠(추적용 Wrapper) | 차단이 안 되고 run 무효 | 1절. 툴 본문을 바꾼다 |
| 차단 메시지를 받으면 예외를 던져 루프를 끊음 | 에이전트 루프가 중단되어 trace가 기준과 달라짐 | 문자열을 툴 결과로 돌려준다 |
| `final_answer`·`done` 같은 자체 툴을 `internal_tools`에 빠뜨림 | `not a gated suite tool`로 run 무효 | `internal_tools`에 적는다 |
| `tool_start`를 재시도·중첩 호출에서 두 번 기록 | `execution(s) without a gate decision`으로 run 무효 | 실행 지점 한 곳에서만 기록한다 |
| `routes_tools_through_bridge` 선언 누락 | 대시보드에서 비활성, runner가 거부 | `True`로 선언한다 |
| AgentDojo 툴을 `decide`로 판정받고 직접 실행 | 400 | `invoke`를 쓴다 |
| 프레임워크 자체 툴 이름이 AgentDojo 툴과 겹침 | 잘못된 툴 실행 | AgentDojo 툴을 우선한다(browser-use `read_file` 처리 참조) |
