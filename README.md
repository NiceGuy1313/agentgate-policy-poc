# agentgate — AgentDojo 툴 호출 정책 엔진 PoC

툴 응답에 심어진 지시(간접 프롬프트 인젝션)로 에이전트가 송금·반출·삭제를 하는 문제를, **모델 바깥의 결정론적 정책 게이트**(P1–P9)로 막을 수 있는지 AgentDojo v1.2.2(`agentdojo==0.1.35`)에서 검증한다.

- 구조와 설계: [`docs/architecture.md`](docs/architecture.md)
- 정책 규칙: [`docs/policy_spec.md`](docs/policy_spec.md)
- 평가 결과: [`docs/final_report.md`](docs/final_report.md), [`results/dashboard.html`](results/dashboard.html)

## 빠른 시작

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q

# 공격 쌍 하나를 게이트에 통과시키며 호출별 판정 출력 (LLM 호출 없음)
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```

LLM이 필요한 단계는 `.env.example`을 `.env`로 복사해 키를 넣는다.

## 핵심 파일

| 파일 | 내용 |
|---|---|
| [`src/agentgate/runtime.py`](src/agentgate/runtime.py) | 모든 툴 호출이 지나는 게이트 |
| [`src/agentgate/policy/rules.py`](src/agentgate/policy/rules.py) | 정책 P1–P9 |
| [`src/agentgate/provenance.py`](src/agentgate/provenance.py) | 인자 값의 출처 판정 |
| [`src/agentgate/task_spec.py`](src/agentgate/task_spec.py) | 사용자 요청의 허용 범위(TaskSpec) |
| [`config/policies.yaml`](config/policies.yaml) | 정책별 대상 툴·임계값 |

## 폴더 구조

```
src/agentgate/
  policy/        정책과 판정 결합 (LLM 호출 없음)
  llm/           TaskSpec 추출기, ESCALATE 판정 LLM
  agents/        평가 대상 에이전트
  evaluation/    재생 평가, 지표
config/          정책 설정
prompts/         LLM 프롬프트
scripts/         benchmark · taskspec · evaluation · analysis · reporting
tests/           pytest
data/
  benchmark/     AgentDojo에서 추출한 태스크·툴 목록, 공격 유형 레이블, 인자 출처 분석
  specs/         태스크별 TaskSpec (oracle, LLM 추출)
  pairs/         live 평가용 층화 표본 쌍
  judge_cache/   판정 LLM 응답 캐시 (키 없이 재현)
docs/            구조·정책 사양·평가 보고서
results/         평가 결과 (replay, live, judge)
```

## 재현

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

정책을 고치면 `tests/policy/`와 재생 평가를 다시 돌려 이전 결과와 비교하고, 결과는 새 버전 경로(`results/.../v2`)에 둔다.
