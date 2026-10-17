# agentgate — AgentDojo 툴 호출 정책 Gate

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)를 따라 송금·반출·삭제를 하지 않도록, 모든 툴 호출을 **실행 직전에 결정론 정책(P1–P9)으로 검사**하는 Gate다. AgentDojo v1.2.2(`agentdojo==0.1.35`)와 외부 에이전트 프레임워크(smolagents 등)에서 검증한다.

> **버전**: v1(P1–P9), v2(판별기 + Trusted LLM), v3·v3.1(ToolSpec + 정책 문서 + 판정 LLM)은 `src/agentgate/v1`·`v2`·`v3`에 나뉘어 있다. v2·v3는 재실험 전이며, 이전 결과 문서는 [`archive/v2_v3_results_2026-10-17/`](archive/v2_v3_results_2026-10-17/README.md)에 있다.

자세한 내용은 [`docs/architecture.md`](docs/architecture.md)(구조·Wrapper 규약), [`docs/policy_spec.md`](docs/policy_spec.md)(정책 상세), [`docs/final_report.md`](docs/final_report.md)(평가 결과)에 있다. 정적 결과 요약은 [`results/dashboard.html`](results/dashboard.html)이다.

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
| `AGENTGATE_JUDGE_MODEL` | ESCALATE 판정 LLM(C4·C5·C6·C7) |
| `AGENTGATE_TOOLSPEC_MODEL` | v3 ToolSpec 추출 LLM(기본 `claude-haiku-5-5`) |
| `AGENTGATE_V2_DETECTOR` | v2 판별기(비우면 `config/v2/detector.yaml` 기본 `horizon-base`, `piguard:chunks`, `none`) |

## 3. 대시보드 실행

```bash
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
```

브라우저에서 http://127.0.0.1:8000 을 연다.

1. 왼쪽에서 **에이전트**, **모델**, **도메인 → 사용자 작업 → 공격**을 고른다.
2. **실행**을 누른다. 기본값으로 **GATE ON**(Gate 적용, C1)과 **GATE OFF**(Gate 없음, C0)가 나란히 실행된다.
3. 툴 호출마다 Gate 판정(ALLOW / ESCALATE / BLOCK, 정책 ID, 이유)이 붙는다. 끝나면 Result에 `Utility`, `ASR`, `Blocked / Calls`가 표시된다.

- 공격 문구, TaskSpec 출처, 최대 단계, ESCALATE 처리(C1/C2/C4), 모델 직접 입력은 "고급 설정"에 있다.
- 컬럼 경계를 드래그하면 너비가 바뀐다. 상단 버튼으로 설정 패널을 접거나 너비를 초기화한다.
- 실행 기록은 `dashboard/runs/`(git 제외)에 저장된다. 보고서 수치에는 쓰지 않는다.

배치 평가는 같은 시나리오를 여러 쌍으로 돌린다.

```bash
python scripts/evaluation/live_evaluation.py --framework smolagents --model ollama:gpt-oss:120b \
  --pilot --conditions C0 C1 --out results/v1/live/<run>
python scripts/analysis/summarize_live.py --run results/v1/live/<run>
```

## 4. 정책

판정 코드는 [`src/agentgate/v1/policies.py`](src/agentgate/v1/policies.py)에 있다. 정책별 대상 툴과 임계값은 [`config/v1/policies.yaml`](config/v1/policies.yaml)에서 바꾼다.

| 정책 | 막는 것 | 코드 |
|---|---|---|
| P1 외부 목적지 출처 | 프롬프트에 없는 수신자·계좌·URL로 보내기 | [`DestinationProvenancePolicy`](src/agentgate/v1/policies.py) |
| P2 민감 데이터 반출 | 요청 없이 민감한 읽기 결과를 외부로 전송 | [`SensitiveEgressPolicy`](src/agentgate/v1/policies.py) |
| P3 대량 반출 | 한 번의 전송에 여러 읽기 결과 묶어 보내기 | [`BulkEgressPolicy`](src/agentgate/v1/policies.py) |
| P4 금전 | 상한 초과·근거 없는 금액, 반복 송금 | [`MoneyTransferPolicy`](src/agentgate/v1/policies.py) |
| P5 삭제 | 보낸 메일 삭제, 지정하지 않은 파일 삭제 | [`DeletionPolicy`](src/agentgate/v1/policies.py) |
| P6 계정·권한 | 요청 없는 비밀번호·사용자 정보·권한 변경 | [`AccountPermissionPolicy`](src/agentgate/v1/policies.py) |
| P7 비신뢰 내용 | 툴 응답 속 지시·링크 전파, 요청 없는 URL 방문 | [`UntrustedContentPolicy`](src/agentgate/v1/policies.py) |
| P8 예약 | 요청 없는 호텔·차량·식당 예약 | [`ReservationPolicy`](src/agentgate/v1/policies.py) |
| P9 일정 | 요청 없는 일정 생성·변경·취소 | [`CalendarChangePolicy`](src/agentgate/v1/policies.py) |

판정은 BLOCK > ESCALATE > ALLOW 순으로 가장 강한 것을 고른다([`engine.py`](src/agentgate/gate/engine.py)). 모든 툴 호출은 [`GatedRuntime.run_function`](src/agentgate/gate/runtime.py) 한 곳을 지난다. v2·v3 추가 정책(P0, P10, P11, P12, v3.1 수정)은 [`docs/policy_spec.md`](docs/policy_spec.md) 5절에 있다.

정책을 수정하면 다음을 지킨다.
- `tests/v1/`·`tests/v2/`·`tests/v3/`의 해당 테스트를 고치고 `python -m pytest -q`를 통과시킨다.
- `scripts/evaluation/replay_evaluation.py`를 새 결과 경로로 돌려 이전 결과와 비교한다.

## 5. 실험 조건과 재현

조건은 [`src/agentgate/conditions.py`](src/agentgate/conditions.py)에 있다. C0(방어 없음)·C3(AgentDojo 내장 방어)은 기준선, C1·C2·C4는 v1, C5는 v2, C6·C7·C7n·C8은 v3다. C8은 ESCALATE만이 아니라 모든 effect 호출을 판정 LLM이 최종 판단한다. 버전을 비교할 때는 같은 실행에 C0을 함께 넣는다.

규모: AgentDojo v1.2.2는 사용자 작업 97개 × 같은 suite의 injection task(workspace 14, travel 7, banking 9, slack 5) = 공격 쌍 949개다. `--pairs`를 주지 않으면 live도 이 전체(공격 없음 97 + 공격 949)를 돌린다. `--pairs @data/agentdojo/pairs/strat3_seed0.txt`는 사용자 작업마다 공격 3개를 고른 층화 표본(97 + 291)이다.

```bash
# 재생 평가 (에이전트 없음, ground truth 궤적)
python scripts/evaluation/replay_evaluation.py --condition C1 --spec oracle --out results/v1/replay/oracle
python scripts/analysis/summarize_replay.py --run results/v1/replay/oracle

# v1 live (전체 규모, important_instructions)
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --conditions C0 C1 C2 C4 \
  --attack important_instructions --out results/v1/live/<run>

# v2 live (판별기: pip install -e '.[detector]')
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --conditions C0 C5 \
  --attack important_instructions --out results/v2/live/<run>

# v3 (TaskSpec·ToolSpec은 Haiku, 둘 다 Message Batches로 미리 추출해 디스크 캐시를 채운다)
python scripts/taskspec/extract_llm_specs.py --version v3
python scripts/v3/toolspec_evaluation.py --out results/v3/toolspec
python scripts/evaluation/replay_evaluation.py --condition C8 --spec llm:claude-haiku-5-5 --out results/v3/replay/C8
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --conditions C0 C7 C8 \
  --attack important_instructions --out results/v3/live/<run>

python scripts/analysis/check_enforcement.py --run results/<version>/live/<run>
python scripts/analysis/summarize_live.py --run results/<version>/live/<run>
python scripts/reporting/build_report.py      # docs/final_report.md, results/figures, results/dashboard.html (v1)
```

## 6. 폴더 구조

```
src/agentgate/
  gate/          버전 공통 Gate: 정책 primitive·엔진·값 출처·TaskSpec·GatedRuntime (LLM 호출 없음)
  agentdojo/     AgentDojo 테스트: suite 로딩, live pipeline·채점, 재생, 공격 궤적, 지표, 층화 표본
  agents/        평가 대상 에이전트(Claude·Ollama·OpenAI·Scripted)와 프레임워크 어댑터
  harness/       외부 프레임워크 연결, Wrapper 규약 v1
  llm/           LLM client, TaskSpec 추출기, 판정 LLM 공용 입출력
  v1/  v2/  v3/  버전별 정책·판정 LLM·런타임 확장·조건 조립(experiment.py)
  conditions.py  실험 조건 등록부 (C0–C8)
config/          agentdojo/(툴 역할·민감 읽기·지목 자료 규칙), v1/, v2/, v3/, frameworks.yaml
prompts/         v1/, v2/, v3/
scripts/         agentdojo · taskspec · evaluation · analysis · reporting · v2 · v3 · cloud
tests/           공통 테스트, v1/, v2/, v3/, frameworks/
data/
  agentdojo/     벤치마크 추출(태스크·툴·공격 유형 레이블), oracle TaskSpec, 층화 표본 쌍
  v1/            LLM TaskSpec(specs/), LLM 응답 캐시(cache/)
  v3/atr/        v3 정책 문서 근거(ATR test-corpora 분류)
results/         v1/{replay,live,judge}, figures/, dashboard.html (v2·v3는 재실험 후 results/v2·v3/)
docs/            구조(architecture.md), 정책 사양(policy_spec.md), 결과 보고서(final_report.md)
archive/         이전 v2·v3 결과 문서 백업
dashboard/       실시간 Gate 대시보드 (FastAPI)
```
