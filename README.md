# agentgate — AgentDojo 툴 호출 정책 Gate

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)를 따라 송금·반출·삭제를 하지 않도록, 모든 툴 호출을 **실행 직전에 결정론 정책(P1–P9)으로 검사**하는 Gate다. AgentDojo v1.2.2(`agentdojo==0.1.35`)와 외부 에이전트 프레임워크(smolagents 등)에서 검증한다.

> **후속 구성**: [`v2_detector_trusted_llm/`](v2_detector_trusted_llm/README.md)(판별기 + Trusted LLM, 폐기)와 [`v3_trusted_llm_policy_doc/`](v3_trusted_llm_policy_doc/README.md)(ToolSpec + 정책 문서 + 판정 LLM)에 버전별 구조·코드·결과를 나눠 두었다. 작업 명세는 [`명세서.md`](명세서.md).

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
  --pilot --conditions C0 C1 --out results/live/v2/<run>
python scripts/analysis/summarize_live.py --run results/live/v2/<run>
```

## 4. 정책

판정 코드는 [`src/agentgate/policy/rules.py`](src/agentgate/policy/rules.py)에 있다. 정책별 대상 툴과 임계값은 [`config/policies.yaml`](config/policies.yaml)에서 바꾼다.

| 정책 | 막는 것 | 코드 |
|---|---|---|
| P1 외부 목적지 출처 | 프롬프트에 없는 수신자·계좌·URL로 보내기 | [`DestinationProvenancePolicy`](src/agentgate/policy/rules.py#L41) |
| P2 민감 데이터 반출 | 요청 없이 민감한 읽기 결과를 외부로 전송 | [`SensitiveEgressPolicy`](src/agentgate/policy/rules.py#L58) |
| P3 대량 반출 | 한 번의 전송에 여러 읽기 결과 묶어 보내기 | [`BulkEgressPolicy`](src/agentgate/policy/rules.py#L106) |
| P4 금전 | 상한 초과·근거 없는 금액, 반복 송금 | [`MoneyTransferPolicy`](src/agentgate/policy/rules.py#L149) |
| P5 삭제 | 보낸 메일 삭제, 지정하지 않은 파일 삭제 | [`DeletionPolicy`](src/agentgate/policy/rules.py#L196) |
| P6 계정·권한 | 요청 없는 비밀번호·사용자 정보·권한 변경 | [`AccountPermissionPolicy`](src/agentgate/policy/rules.py#L221) |
| P7 비신뢰 내용 | 툴 응답 속 지시·링크 전파, 요청 없는 URL 방문 | [`UntrustedContentPolicy`](src/agentgate/policy/rules.py#L247) |
| P8 예약 | 요청 없는 호텔·차량·식당 예약 | [`ReservationPolicy`](src/agentgate/policy/rules.py#L293) |
| P9 일정 | 요청 없는 일정 생성·변경·취소 | [`CalendarChangePolicy`](src/agentgate/policy/rules.py#L315) |

판정은 BLOCK > ESCALATE > ALLOW 순으로 가장 강한 것을 고른다([`engine.py`](src/agentgate/policy/engine.py)). 모든 툴 호출은 [`GatedRuntime.run_function`](src/agentgate/runtime.py) 한 곳을 지난다.

정책을 수정하면 다음을 지킨다.
- `tests/policy/`의 해당 테스트를 고치고 `python -m pytest -q`를 통과시킨다.
- `scripts/evaluation/replay_evaluation.py --spec oracle`을 새 버전 경로로 돌려 이전 결과와 비교한다.

## 5. 재현

```bash
# 재생 평가 (LLM 없음)
python scripts/evaluation/replay_evaluation.py --spec oracle --out results/replay/v1/oracle
python scripts/analysis/summarize_replay.py --run results/replay/v1/oracle

# 실제 에이전트 평가 (키 필요)
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --pairs @data/pairs/strat3_seed0.txt \
  --conditions C0 C1 C2 C4 --out results/live/v1/<run>
python scripts/analysis/summarize_live.py --run results/live/v1/<run>   # 커밋된 run은 runs.tar.gz를 먼저 푼다

# 보고서·그림·대시보드 재생성
python scripts/reporting/build_report.py
```

## 6. 폴더 구조

```
src/agentgate/
  policy/        정책과 판정 결합 (LLM 호출 없음)
  llm/           TaskSpec·ToolSpec 추출기, ESCALATE 판정 LLM (v1, v2, v3)
  detector/      툴 응답 인젝션 판별기 래퍼 (v2만, PIGuard·ProtectAI)
  agents/        평가 대상 에이전트 (frameworks/: smolagents 어댑터)
  harness/       외부 프레임워크 연결, Wrapper 규약 v1
  evaluation/    재생 평가, 지표
config/          정책 설정, 프레임워크 등록(frameworks.yaml), 판별기 설정(detector.yaml)
dashboard/       실시간 Gate 대시보드 (FastAPI)
prompts/         LLM 프롬프트
scripts/         benchmark · taskspec · evaluation · analysis · reporting · cloud(클라우드 세션 준비)
tests/           pytest
data/
  benchmark/     AgentDojo에서 추출한 태스크·툴 목록, 공격 유형 레이블, 인자 출처 분석
  specs/         태스크별 TaskSpec (oracle, LLM 추출)
  pairs/         live 평가용 층화 표본 쌍
  judge_cache/   판정 LLM 응답 캐시 (키 없이 재현)
docs/            구조·정책 사양·평가 보고서 (v1 중심, 6절·9절·5절에서 버전별 폴더로 연결)
v2_detector_trusted_llm/    v2 구조·결과(README, RESULTS)와 코드 읽기용 사본
v3_trusted_llm_policy_doc/  v3·v3.1 구조·결과(README, RESULTS)와 코드 읽기용 사본
results/         평가 결과 (replay, live, judge)
```
