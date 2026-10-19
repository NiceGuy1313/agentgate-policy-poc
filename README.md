# agentgate — AgentDojo 툴 호출 정책 Gate

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)를 따라 송금·반출·삭제를 하지 않도록, effect 툴 호출(상태 변경·외부 전송·URL 방문)을 **실행 직전에 룰북(R1–R10) 기준으로 판정**하는 Gate다. AgentDojo v1.2.2(`agentdojo==0.1.35`)와 외부 에이전트 프레임워크(smolagents 등)에서 검증한다.

> **보관본**: v1(AgentDojo 맞춤 결정론 정책 P1–P9)은 [`archive/v1.bak/`](archive/v1.bak/README.md), v2(판별기 + Trusted LLM)는 [`archive/v2.bak/`](archive/v2.bak/README.md)에 보관한다. 결과는 `results/RESULTS.md`에 둔다. 이전 C9·C9n(Sonnet 판정 LLM) 결과는 [`archive/v3_sonnet_2026-10-18/`](archive/v3_sonnet_2026-10-18/RESULTS.md), 그 전 결과 문서는 [`archive/v2_v3_results_2026-10-17/`](archive/v2_v3_results_2026-10-17/README.md)에 있다.

자세한 내용은 [`docs/architecture.md`](docs/architecture.md)(구조·Wrapper 규약), [`docs/policy_spec.md`](docs/policy_spec.md)(정책 상세)에 있다.

## 1. 설치

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,dashboard,smolagents,mini-swe-agent]'
python -m pytest -q
```

browser-use는 `anthropic`·`openai` 버전이 agentgate와 충돌하므로 별도 venv에 설치한다. 대시보드와 배치 평가는 [`config/frameworks.yaml`](config/frameworks.yaml)의 `python:` 경로로 이 venv를 찾아 하위 프로세스에서 실행한다. Chrome이 필요하다.

```bash
python3.13 -m venv .venv-browser-use
.venv-browser-use/bin/pip install "browser-use @ git+https://github.com/NiceGuy1313/browser-use@f39fb5c3a6fc611688007819c71ef104bd6ca01a" \
  agentdojo==0.1.35 pyyaml requests
.venv-browser-use/bin/pip install --no-deps -e .
.venv-browser-use/bin/python -m agentgate.harness.conformance --framework browser-use   # LLM 없이 8/8 확인
```

연결된 에이전트 프레임워크는 AgentDojo native, smolagents, mini-swe-agent, browser-use다. 연결 방식은 [`docs/architecture.md`](docs/architecture.md) 8절에 있다.

## 2. 환경 변수

`.env.example`을 `.env`로 복사해 값을 넣는다. `.env`는 git에 올라가지 않는다. 같은 이름의 환경 변수를 직접 지정해도 되고, 그 값이 `.env`보다 우선한다.

```bash
cp .env.example .env
# 또는
export OLLAMA_API_KEY=<키>
```

| 변수 | 용도 |
|---|---|
| `OLLAMA_API_KEY` | Ollama Cloud 모델(gpt-oss:120b, mistral-large-3:675b) 호출. 대시보드 기본 모델에 필요 |
| `OLLAMA_HOST` | 기본 `https://ollama.com`. 로컬 서버는 `http://localhost:11434`(키 불필요) |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` | 해당 제공자 모델을 쓸 때 |
| `AGENTGATE_AGENT_PROVIDER` · `AGENTGATE_AGENT_MODEL` | 배치 평가에서 쓸 에이전트 모델 |
| `AGENTGATE_LLM_PROVIDER` · `AGENTGATE_LLM_MODEL` | TaskSpec 추출 LLM(캐시가 있으면 키 없이 재사용) |
| `AGENTGATE_JUDGE_PROVIDER` · `AGENTGATE_JUDGE_MODEL` · `AGENTGATE_JUDGE_THINK` | 판정 LLM(gate_llm_judge). 비우면 에이전트 설정(`AGENTGATE_AGENT_*`, 예: Ollama `gpt-oss:120b`)을 따른다 |
| `AGENTGATE_TOOLSPEC_MODEL` | ToolSpec 추출 LLM(기본 `claude-haiku-5-5`) |

## 3. 대시보드 실행

```bash
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
```

브라우저에서 http://127.0.0.1:8000 을 연다.

1. 왼쪽에서 **에이전트**, **모델**, **도메인 → 사용자 작업 → 공격**을 고른다.
2. **실행**을 누른다. 기본값으로 **GATE ON**(Gate 적용, `gate_static_rules`)과 **GATE OFF**(Gate 없음, `no_gate`)가 나란히 실행된다.
3. 툴 호출마다 Gate 판정(ALLOW / BLOCK, 규칙 ID, 이유)이 붙는다. 끝나면 Result에 `Utility`, `ASR`, `Blocked / Calls`가 표시된다.

- 공격 문구, TaskSpec 출처, 최대 단계, Gate 판정기(`gate_static_rules` / `gate_llm_judge`), 모델 직접 입력은 "고급 설정"에 있다.
- 컬럼 경계를 드래그하면 너비가 바뀐다. 상단 버튼으로 설정 패널을 접거나 너비를 초기화한다.
- 실행 기록은 `dashboard/runs/`(git 제외)에 저장된다. 보고서 수치에는 쓰지 않는다.

배치 평가는 같은 시나리오를 여러 쌍으로 돌린다.

```bash
python scripts/evaluation/live_evaluation.py --framework smolagents --model ollama:gpt-oss:120b \
  --pilot --conditions no_gate gate_static_rules --out results/live/<run>
python scripts/analysis/summarize_live.py --run results/live/<run>
```

## 4. 정책

룰북은 [`config/rulebook.yaml`](config/rulebook.yaml)(R1–R10), 툴 인자 역할은 [`config/tool_args.yaml`](config/tool_args.yaml)이다. 정적 검사 코드는 [`src/agentgate/policy_gate/rule_checker.py`](src/agentgate/policy_gate/rule_checker.py), 판정 LLM은 [`src/agentgate/policy_gate/llm_judge.py`](src/agentgate/policy_gate/llm_judge.py)다. 모든 툴 호출은 [`GatedRuntime.run_function`](src/agentgate/core/runtime.py) 한 곳을 지나고, 판정은 [`PolicyGateRuntime`](src/agentgate/policy_gate/runtime.py)이 한다. Gate 공통 설정은 [`config/gate.yaml`](config/gate.yaml)이다.

정책을 수정하면 다음을 지킨다.
- `tests/policy_gate/`의 해당 테스트를 고치고 `python -m pytest -q`를 통과시킨다.
- `scripts/evaluation/replay_evaluation.py`를 새 결과 경로로 돌려 이전 결과와 비교한다.

## 5. 실험 조건과 재현

조건은 [`src/agentgate/experiment/conditions.py`](src/agentgate/experiment/conditions.py)에 있다. `no_gate`(방어 없음)는 기준선, `gate_llm_judge`(판정 LLM + 룰북)와 `gate_static_rules`(룰북 정적 코드, 판정에 LLM 호출 없음)는 정책 Gate다. 판정은 ALLOW / BLOCK 두 가지뿐이다. 조건을 비교할 때는 같은 실행에 `no_gate`를 함께 넣는다.

규모: AgentDojo v1.2.2는 사용자 작업 97개 × 같은 suite의 injection task(workspace 14, travel 7, banking 9, slack 5) = 공격 쌍 949개다. `--pairs`를 주지 않으면 live도 이 전체(공격 없음 97 + 공격 949)를 돌린다. `--pairs @data/agentdojo/pairs/strat3_seed0.txt`는 사용자 작업마다 공격 3개를 고른 층화 표본(97 + 291)이다.

```bash
# TaskSpec·ToolSpec은 Haiku, 둘 다 Message Batches로 미리 추출해 디스크 캐시(data/cache/)를 채운다
python scripts/taskspec/extract_llm_specs.py
python scripts/analysis/toolspec_evaluation.py --out results/toolspec

# 재생 평가 (에이전트 없음, ground truth 궤적)
python scripts/evaluation/replay_evaluation.py --condition gate_static_rules --spec oracle --out results/replay/oracle

# live (전체 규모, important_instructions)
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --conditions no_gate gate_llm_judge gate_static_rules \
  --attack important_instructions --out results/live/<run>

python scripts/analysis/check_enforcement.py --run results/live/<run>
python scripts/analysis/summarize_live.py --run results/live/<run>
python scripts/analysis/live_significance.py --run results/live/<run>
python scripts/analysis/summarize_results.py --run results/live/<run>   # results/RESULTS.md
```

## 6. 폴더 구조

```
src/agentgate/
  core/          Gate 공통부: 자료형·기본 엔진·값 출처·TaskSpec·GatedRuntime (LLM 호출 없음)
  policy_gate/   정책 Gate: 룰북 정적 검사기, 판정 LLM, ToolSpec 추출기, PolicyGateRuntime, 조건 조립
  experiment/    실험 조건 등록부 (no_gate, gate_llm_judge, gate_static_rules)
  scenarios/     AgentDojo 시나리오: suite 로딩, live pipeline·채점, 재생, 공격 궤적, 지표, 층화 표본
  agents/        평가 대상 에이전트(Claude·Ollama·OpenAI·Scripted)
  adapters/      에이전트 프레임워크 어댑터(native, smolagents, mini-swe-agent, browser-use, contract)
  harness/       외부 프레임워크 연결, Wrapper 규약 v1
  llm/           LLM client, TaskSpec 추출기, 판정 LLM 공용 입출력
config/          gate.yaml, rulebook.yaml, tool_args.yaml, agentdojo/(툴 역할·민감 읽기·지목 자료 규칙), frameworks.yaml
prompts/         extract_taskspec.md, extract_toolspec.md
scripts/         agentdojo · taskspec · evaluation · analysis · cloud
tests/           core/, policy_gate/, frameworks/, 공통 테스트
data/
  agentdojo/     벤치마크 추출(태스크·툴·공격 유형 레이블), oracle TaskSpec, 층화 표본 쌍
  atr/           룰북 근거(ATR test-corpora 분류)
  cache/         LLM 응답 캐시·저장된 LLM TaskSpec (git 제외)
results/         RESULTS.md (live·replay 원본 기록은 git 제외)
docs/            구조(architecture.md), 정책 사양(policy_spec.md)
archive/         v1.bak/·v2.bak/(보관한 이전 버전), 이전 결과 문서 백업
dashboard/       실시간 Gate 대시보드 (FastAPI)
```
