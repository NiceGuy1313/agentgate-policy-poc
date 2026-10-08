# agentgate — AgentDojo 툴 호출 정책 엔진 PoC

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)를 따라 송금·데이터 반출·삭제를 하는 문제를, **모델 바깥의 결정론적 정책 게이트**(P1–P9)로 막을 수 있는지 AgentDojo v1.2.2(`agentdojo==0.1.35`)에서 검증한다.

| 문서 | 내용 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 게이트 구조, 판정 입력(TaskSpec·출처), 설계 결정, 한계 |
| [`docs/policy_spec.md`](docs/policy_spec.md) | 위협 분류, 정책 P1–P9 규칙, TaskSpec 스키마 |
| [`docs/final_report.md`](docs/final_report.md) | 평가 결과(스크립트가 생성) |
| [`results/dashboard.html`](results/dashboard.html) | 공격 쌍·정상 태스크별 호출 판정 근거를 브라우저로 탐색 |

## 빠른 시작

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q

# 공격 쌍 하나를 게이트에 통과시키며 호출별 판정과 근거 출력 (LLM 호출 없음)
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

LLM이 필요한 단계는 `.env.example`을 `.env`로 복사해 키를 넣는다(`.env`는 git에서 제외).

## 코드 지도

### 정책 모듈 — 판정 로직 (LLM 호출 없음)

| 위치 | 주요 심볼 | 역할 |
|---|---|---|
| [`src/agentgate/policy/rules.py`](src/agentgate/policy/rules.py) | `DestinationProvenancePolicy`(P1) … `CalendarChangePolicy`(P9), `POLICY_CLASSES` | 정책 P1–P9 구현 |
| [`src/agentgate/policy/engine.py`](src/agentgate/policy/engine.py) | `PolicyEngine`, `EngineResult`, `combine_verdicts` | 정책 실행과 판정 결합(BLOCK > ESCALATE > ALLOW) |
| [`src/agentgate/policy/base.py`](src/agentgate/policy/base.py) | `Policy`, `ToolCall`, `PolicyDecision`, `PolicyContext`, `Verdict`, `SessionState` | 정책 기반 클래스와 판정 자료형, 세션 상태(P4·P5) |
| [`src/agentgate/provenance.py`](src/agentgate/provenance.py) | `ValueIndex`, `Origin`, `ValueIndex.origin_of` | 인자 값의 출처 판정, 텍스트 겹침 계산 |
| [`src/agentgate/normalization.py`](src/agentgate/normalization.py) | `normalize_value`, `haystack_contains` | 이메일·URL·IBAN·금액 정규화와 비교 |
| [`src/agentgate/task_spec.py`](src/agentgate/task_spec.py) | `TaskSpec`, `load_task_spec` | 사용자 요청의 구조화(정책 입력) |
| [`config/policies.yaml`](config/policies.yaml) | `P1`…`P9`, `runtime` | 정책별 대상 툴·임계값, ESCALATE 처리 방식 |
| [`config/named_source_rules.yaml`](config/named_source_rules.yaml) | `by_argument`, `trusted_tools` | 지목 자료 판정 규칙, 신뢰 툴 목록 |
| [`config/sensitive_reads.yaml`](config/sensitive_reads.yaml) · [`config/tool_roles.yaml`](config/tool_roles.yaml) | — | 민감 읽기 범주(P2·P3), 효과 툴 분류 |

### 게이트와 AgentDojo 연동

| 위치 | 주요 심볼 | 역할 |
|---|---|---|
| [`src/agentgate/runtime.py`](src/agentgate/runtime.py) | `GatedRuntime.run_function` | 모든 툴 호출이 지나는 단일 관문(`FunctionsRuntime` 서브클래스) |
| [`src/agentgate/pipeline.py`](src/agentgate/pipeline.py) | `build_pipeline`, `TaskSpecElement`, `run_live_task` | AgentDojo 파이프라인에 TaskSpec 단계와 게이트 연결 |
| [`src/agentgate/suites.py`](src/agentgate/suites.py) | `load_suite`, `describe_tools`, `task_number` | AgentDojo suite 로드 도우미 |
| [`src/agentgate/config.py`](src/agentgate/config.py) | `PROJECT_ROOT`, `load_policy_config`, `load_dotenv` | 경로, 설정 로더, `.env` 로더 |

### LLM 구성요소와 평가 대상 에이전트

| 위치 | 주요 심볼 | 역할 |
|---|---|---|
| [`src/agentgate/llm/taskspec_extractor.py`](src/agentgate/llm/taskspec_extractor.py) | `TaskSpecExtractor` | 프롬프트·툴 목록만으로 TaskSpec 추출(캐시) |
| [`src/agentgate/llm/escalation_judge.py`](src/agentgate/llm/escalation_judge.py) | `EscalationJudge` | ESCALATE 호출을 판정하는 LLM(조건 C4, 캐시) |
| [`src/agentgate/llm/client.py`](src/agentgate/llm/client.py) | `LLMClient`, `OllamaClient` | Anthropic·OpenAI·Ollama 호출 |
| [`src/agentgate/agents/`](src/agentgate/agents/) | `ClaudeAgentLLM`, `OllamaAgentLLM`, `ScriptedAgent`, `make_llm`, `spec_provider`, `UsageMeter` | 평가 대상 에이전트(실제 모델, 결정론 대역)와 실제 실행용 생성 도우미(`factory.py`) |
| [`prompts/`](prompts/) | `extract_taskspec.md`, `judge.md` | LLM 프롬프트 템플릿 |

### 평가

| 위치 | 주요 심볼 | 역할 |
|---|---|---|
| [`src/agentgate/evaluation/replay.py`](src/agentgate/evaluation/replay.py) | `replay_attack_pair`, `replay_benign_task` | 정답 궤적을 게이트에 재생(LLM 없음) |
| [`src/agentgate/evaluation/attack_trajectories.py`](src/agentgate/evaluation/attack_trajectories.py) | `plan_attack_calls` | 공격 성공 시 에이전트가 할 호출(정답 없는 태스크는 직접 작성) |
| [`src/agentgate/evaluation/metrics.py`](src/agentgate/evaluation/metrics.py) | `detection_table`, `count_benign_false_positives`, `wilson`, `mcnemar_exact`, `TYPE_NAMES` | 탐지율·오탐 집계, 신뢰구간·검정 |
| [`src/agentgate/evaluation/pairs.py`](src/agentgate/evaluation/pairs.py) | `stratified_pairs` | 실제 실행용 층화 표본 쌍 생성 |
| [`tests/`](tests/) | `tests/policy/test_p1.py` … `test_p9.py` | 정책별 합성 trace 테스트, 게이트 우회·설정 일관성 테스트 |

## 스크립트

| 폴더 | 스크립트 | 하는 일 |
|---|---|---|
| [`scripts/catalog/`](scripts/catalog/) | `build_catalog.py`, `analyze_provenance.py`, `check_agentdojo_setup.py` | 태스크 카탈로그·인자 출처 분석(`data/catalog/`), 설치 확인 |
| [`scripts/taskspec/`](scripts/taskspec/) | `seed_oracle_specs.py`, `review_oracle_specs.py`, `extract_llm_specs.py`, `compare_specs.py` | oracle TaskSpec 작성·검토, LLM 추출과 비교 |
| [`scripts/evaluation/`](scripts/evaluation/) | `replay_evaluation.py`, `live_evaluation.py`, `judge_evaluation.py`, `make_stratified_pairs.py` | 재생 평가, 실제 에이전트 실행(C0–C4), 판정 LLM 단독 평가, 층화 표본 생성 |
| [`scripts/analysis/`](scripts/analysis/) | `summarize_replay.py`, `summarize_live.py`, `check_enforcement.py`, `live_significance.py`, `inspect_trace.py` | 결과 집계, 집행 무결성 점검, 신뢰구간·검정, 판정 추적 |
| [`scripts/reporting/`](scripts/reporting/) | `build_report.py` | `docs/final_report.md`, `results/figures/`, `results/dashboard.html` 생성 |

## 재현

```bash
# 정답 궤적 재생 (LLM 없음)
python scripts/evaluation/replay_evaluation.py --spec oracle --out results/replay/v1/oracle
python scripts/analysis/summarize_replay.py --run results/replay/v1/oracle

# 실제 에이전트 (키 필요). 비용 확인 후 실행한다.
python scripts/evaluation/live_evaluation.py --agent llm --spec llm --pairs @data/pairs/strat3_seed0.txt \
  --conditions C0 C1 C2 C4 --out results/live/v1/<run>
python scripts/analysis/check_enforcement.py --run results/live/v1/<run>
python scripts/analysis/summarize_live.py --run results/live/v1/<run>

# 보고서·그림·대시보드 재생성
python scripts/reporting/build_report.py
```

## 데이터와 결과

| 위치 | 내용 |
|---|---|
| [`data/catalog/`](data/catalog/) | 태스크 카탈로그, 공격 유형 레이블, 인자 출처 분석 |
| [`data/specs/oracle/`](data/specs/oracle/) · [`data/specs/llm/`](data/specs/llm/) | oracle TaskSpec 97개, LLM 추출 TaskSpec |
| [`data/specs/llm_cache/`](data/specs/llm_cache/) · [`data/judge_cache/`](data/judge_cache/) | LLM 응답 캐시(키 없이 재현 가능) |
| [`data/pairs/`](data/pairs/) | 층화 표본 쌍 목록 |
| [`results/replay/v1/`](results/replay/v1/) · [`results/live/v1/`](results/live/v1/) · [`results/judge/v1/`](results/judge/v1/) | 재생·실제 실행·판정 LLM 결과. 실제 실행 원본은 `runs.tar.gz`로 압축 |

## 정책을 수정할 때

1. `config/policies.yaml` 또는 `src/agentgate/policy/rules.py`를 고친다.
2. `tests/policy/`에 해당 정책의 테스트를 추가·수정하고 `python -m pytest -q`를 통과시킨다.
3. 재생 평가를 새 버전 경로(`results/replay/v2/...`)로 다시 돌려 이전 결과와 비교한다.
4. 평가 수치를 바꾸는 변경은 먼저 변경안을 공유하고 합의한다.
