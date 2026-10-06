# agentgate — AgentDojo 툴 호출 정책 엔진 PoC

LLM 에이전트가 툴 응답에 심어진 지시(간접 프롬프트 인젝션)에 따라 송금·데이터 반출·삭제를 하는 문제를, **모델 바깥의 결정론적 정책 게이트**로 막을 수 있는지 AgentDojo(v1.2.2)에서 검증한다.

- 결과 요약: [`docs/final_report.md`](docs/final_report.md)
- 결과를 직접 둘러보기: `results/dashboard.html`을 브라우저로 연다. 949개 공격 쌍과 97개 정상 태스크의 툴 호출별 판정 근거를 볼 수 있다.
- 정책 사양: [`docs/policy_spec.md`](docs/policy_spec.md) · 구현: [`docs/policy_impl.md`](docs/policy_impl.md) · 설계 결정: [`docs/design_decisions.md`](docs/design_decisions.md)

## 구조

```
config/        정책 설정(policies.yaml), 툴 역할, 지목 출처 규칙, 민감 읽기 범주
src/agentgate/
  runtime.py   GatedRuntime: 모든 툴 호출이 지나가는 단일 관문 (FunctionsRuntime 서브클래스)
  trace.py     ValueIndex: 인자 값의 출처 판정 (PROMPT / NAMED_SOURCE / OTHER_TOOL / UNSEEN)
  policy/      P1–P9 결정론적 정책과 결합 엔진 (LLM 없음)
  spec.py      TaskSpec (사용자 요청의 구조화)
  llm/         TaskSpec 추출기, ESCALATE 판정기 (LLM은 여기에만)
  pipeline.py  AgentDojo 파이프라인 통합 (TaskSpecElement, ScriptedAgent)
  replay.py    정답 궤적 재생 평가
data/catalog/  태스크 카탈로그, 공격 유형 레이블, 출처 분석
data/specs/    oracle TaskSpec 97개 (프롬프트만 보고 작성)
scripts/       단계별 실행 스크립트 (아래)
results/       재생·파이프라인 결과, 그림, dashboard.html
docs/          사양·노트·단계별 보고서
```

## 설치와 실행

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q
python scripts/replay_eval.py --spec oracle --out results/replay/v1/oracle   # 949쌍 재생, 약 1분
python scripts/summarize_replay.py
python scripts/live_eval.py --agent scripted --spec oracle --conditions C0 C1 C2 C3 --out results/live/v1/scripted
python scripts/check_enforcement.py --run results/live/v1/scripted
python scripts/summarize_live.py --run results/live/v1/scripted
python scripts/build_report.py      # docs/*report*.md, results/figures, results/dashboard.html 재생성
```

### 한 건씩 확인하기

```bash
# 공격 쌍 하나를 게이트에 통과시키며 호출별 판정과 근거 출력
python scripts/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
# 정상 태스크
python scripts/inspect_trace.py benign workspace user_task_25
# 파이프라인 실행 기록 하나 (원본 실행 기록은 압축되어 있다)
tar xzf results/live/v1/scripted/runs.tar.gz -C results/live/v1/scripted
python scripts/inspect_trace.py live results/live/v1/scripted/C1/banking/user_task_0/injection_task_7.json
# 가상의 호출 하나에 대한 판정
python scripts/inspect_trace.py call --prompt "Pay the bill 'bill-december-2023.txt'" \
  --spec-yaml data/specs/oracle/banking/user_task_0.yaml --tool send_money \
  --args '{"recipient": "US133000000121212121212", "amount": 10, "subject": "x", "date": "d"}' \
  --read "read_file=IBAN: US133000000121212121212"
```

### LLM이 필요한 단계 (API 키)

`ANTHROPIC_API_KEY`만 있으면 된다. 클라우드에서는 환경 설정의 환경 변수로, 로컬에서는 `.env.example`을 `.env`로 복사해 넣는다(`.env`는 git에서 제외). 모델 기본값은 추출기·판정기·에이전트 모두 `claude-opus-5-5`이며 `AGENTGATE_*_MODEL`로 바꿀 수 있다(`docs/design_decisions.md` 4절).

```bash
python scripts/extract_specs.py                         # Stage 4: LLM TaskSpec 추출 (캐시됨)
python scripts/compare_specs.py --model <model>         # oracle과 비교 → docs/stage4_spec_comparison.md
python scripts/replay_eval.py --spec llm:<model> --out results/replay/v1/llm_<model>
python scripts/summarize_replay.py --compare results/replay/v1/llm_<model>
python scripts/judge_eval.py                            # Stage 7: 판정기 단독 평가
python scripts/live_eval.py --agent llm --spec llm --pilot --conditions C0 C1 --out results/live/v1/<model>
python scripts/summarize_live.py --run results/live/v1/<model> --price-in <USD/Mtok> --price-out <USD/Mtok>
# 비용 추정 확인·승인 후 전체 실행 (C4 = ESCALATE→judge)
python scripts/live_eval.py --agent llm --spec llm --conditions C0 C1 C2 C3 C4 --out results/live/v1/<model>
python scripts/build_report.py
```
