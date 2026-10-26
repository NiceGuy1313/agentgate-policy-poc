# AgentGate — LLM 에이전트 툴 호출 Policy Gate

> 에이전트 코드에 행동 로그를 삽입하고 누락 여부를 검증하는 도구 **Observatory**는 별도 문서 참고: [`observatory/README.md`](observatory/README.md)

- AgentDojo 시나리오 테스트 Policy Gate 적용
- 판정 기준: Policy R1–R10 ([`docs/rulebook.md`](docs/rulebook.md))
- 판정 결과: `ALLOW` → 툴 실행, `BLOCK` → 차단 메시지를 툴 결과로 반환
- 평가 환경: AgentDojo v1.2.2(`agentdojo==0.1.35`)
- 지원 프레임워크: `native`(AgentDojo pipeline), `smolagents`, `mini-swe-agent`, contract v1 외부 Wrapper

```
에이전트 → Adapter → bridge.invoke → PolicyGateRuntime ─ ALLOW → 환경 툴 실행
                                                    └ BLOCK → 차단 메시지
```

## 1. 설치

- 요구 사항: Python 3.10 이상

```bash
git clone https://github.com/hyu-sslab/agentgate-policy-poc.git
cd agentgate-policy-poc
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,dashboard,smolagents,mini-swe-agent]'
```

## 2. LLM 설정 (로컬 Ollama 기준)

- LLM 사용 환경: 평가 대상 에이전트, TaskSpec 추출기, ToolSpec 추출기, 판정 LLM(`gate_llm_judge`)
- 설정 파일: `.env`(git 제외), 형식은 `.env.example`

- Ollama 서버 준비 및 모델 내려받기

```bash
ollama serve
ollama pull gpt-oss:20b
curl http://localhost:11434/api/tags
```

- `.env` 작성

```bash
cp .env.example .env
```

```bash
OLLAMA_HOST=http://localhost:11434
OLLAMA_API_KEY=
OLLAMA_NUM_CTX=16384

# 평가 대상 에이전트
AGENTGATE_AGENT_PROVIDER=ollama
AGENTGATE_AGENT_MODEL=gpt-oss:20b
AGENTGATE_AGENT_THINK=

# TaskSpec 추출기
AGENTGATE_LLM_PROVIDER=ollama
AGENTGATE_LLM_MODEL=gpt-oss:20b

# ToolSpec 추출기
AGENTGATE_TOOLSPEC_PROVIDER=ollama
AGENTGATE_TOOLSPEC_MODEL=gpt-oss:20b

# 판정 LLM (gate_llm_judge)
AGENTGATE_JUDGE_PROVIDER=
AGENTGATE_JUDGE_MODEL=
```

- Ollama Cloud 사용 시 변경 항목

```bash
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=<Ollama Cloud 키>
AGENTGATE_AGENT_MODEL=gpt-oss:120b
```

- 주의 사항
  - 모델 이름: `ollama list` 출력과 동일하게 기재, Cloud는 `:cloud` 접미사 제외
  - 로컬 서버: `OLLAMA_API_KEY` 비움
  - `OLLAMA_NUM_CTX`: 요청별 context 길이, 서버 기본값이 커서 메모리 부족·CPU 실행 시 축소 (`ollama ps`로 확인)
  - 판정 LLM: 비우면 에이전트 설정 사용, 에이전트가 Ollama일 때 `claude-*` 모델 지정 시 실행 거부
  - 추출 결과 캐시: `data/cache/{taskspec,toolspec,judge}/<모델>/`, 모델 변경 시 새로 추출

## 3. 설치 확인

- LLM 없이 동작하는 단위 테스트
- Gate 경유 검사(conformance): 가짜 LLM 사용, 출력 끝 `N/N passed` 확인

```bash
python -m pytest -q
python -m agentgate.harness.conformance --framework smolagents --conditions no_gate gate_static_rules
```

## 4. 실험 조건

- `no_gate`: 방어 없음, 호출 기록만(기준선)
- `gate_static_rules`: 정적 규칙 코드로 판정, 판정 단계 LLM 호출 없음
- `gate_llm_judge`: 판정 LLM이 Policy 적용

## 5. 실행 — 대시보드

- 브라우저에서 프레임워크·모델·조건 선택 후 실행
- 주소: `http://127.0.0.1:8000`

```bash
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
```

## 6. 실행 — CLI

- 소규모 시험 실행(`--pilot`: banking suite, 사용자 작업 4개 × 공격 3개)

```bash
python -m agentgate.experiment.cli \
  --framework smolagents \
  --conditions no_gate gate_static_rules \
  --model ollama:gpt-oss:20b \
  --pilot \
  --out results/runs/demo
```

- 주요 옵션

```text
--framework     native | smolagents | mini-swe-agent | config/frameworks.yaml 등록 이름
--conditions    no_gate | gate_static_rules | gate_llm_judge (복수 지정 가능)
--model         provider:model (예: ollama:gpt-oss:20b), 생략 시 .env 값
--suites        banking | slack | travel | workspace
--attack        AgentDojo 공격 이름 (기본 important_instructions)
--out           결과 디렉터리
```

## 7. 결과 확인

- 실행 결과: `--out` 디렉터리의 `scenario_summary.json`
- Gate 경유 여부 검사 및 지표 요약

```bash
python scripts/analysis/check_enforcement.py --run results/runs/demo
python scripts/analysis/summarize_live.py --run results/runs/demo
```

- 단일 사례 trace 확인

```bash
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

## 8. Tool Wrapper 삽입 에이전트 연결

- 원칙: 툴을 직접 실행하지 않고 툴 실행 지점에서 `bridge.invoke` 호출
- Gate 밖 툴 실행 발생 시 해당 run 무효(`valid: false`)
- Python 프레임워크: `FrameworkAdapter` 서브클래스 작성 ([`docs/adapter_guide.md`](docs/adapter_guide.md))

```python
from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.harness.tool_schema import gated_callable


class MyAdapter(FrameworkAdapter):
    name = "my-framework"
    routes_tools_through_bridge = True

    def run(self, task, bridge, model, options, max_steps, sink):
        tools = [make_tool(s, fn=gated_callable(bridge, s["name"]))
                 for s in bridge.tool_schemas()]
        output = run_agent(task, tools)
        return FrameworkResult(final_output=output, steps=...)
```

- 외부 프로세스 Wrapper: HTTP contract v1 ([`docs/integration_guide.md`](docs/integration_guide.md), 예시 [`examples/contract_wrapper/minimal_agent.py`](examples/contract_wrapper/minimal_agent.py))
- 클라이언트: `src/agentgate/harness/agentgate_client.py` 복사 사용, `invoke`가 툴 실행 대신 결과 또는 차단 메시지 반환

```python
from agentgate_client import GateSession

gate = GateSession.from_env()
result = gate.invoke(tool_name, tool_args)
gate.finish(final_answer)
```

- 등록 및 검사

```yaml
# config/frameworks.yaml
frameworks:
  my-framework:
    adapter: my_package.my_adapter:MyAdapter
```

```bash
python -m agentgate.harness.conformance --framework my-framework --conditions no_gate gate_static_rules
```

## 9. 문서·결과

- [`docs/architecture.md`](docs/architecture.md): 모듈 구조, 단일 집행 지점, fail-closed
- [`docs/rulebook.md`](docs/rulebook.md): Policy R1–R10, TaskSpec·ToolSpec
- [`docs/adapter_guide.md`](docs/adapter_guide.md): Adapter 부착 위치와 절차
- [`docs/integration_guide.md`](docs/integration_guide.md): 외부 Wrapper 규약(contract v1)
- [`results/RESULTS.md`](results/RESULTS.md): 프레임워크별 실험 결과
- [`results/COMPLIANCE.md`](results/COMPLIANCE.md): 정책 준수율 분석
- [`observatory/README.md`](observatory/README.md): Observatory(로그 삽입·검증 도구) 사용법

## 10. 폴더 구조

```text
src/agentgate/   core · policy_gate · environments · adapters · harness · experiment · scenarios · agents · llm
config/          gate.yaml · rulebook.yaml · frameworks.yaml · agentdojo/
prompts/         TaskSpec·ToolSpec 추출 프롬프트
scripts/         평가·분석 스크립트
dashboard/       대시보드(FastAPI)
examples/        contract v1 참조 Wrapper
results/         RESULTS.md · COMPLIANCE.md · figures/ · summary/
observatory/     로그 삽입·검증 도구(Observatory), 대상 에이전트 저장소에 복사해 사용
```
