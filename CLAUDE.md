# CLAUDE.md — AgentGate 작업 규칙

LLM 에이전트의 effect 툴 호출을 실행 직전에 룰북(R1–R10) 기준으로 ALLOW / BLOCK 판정하는 Policy Gate다. 평가 시나리오는 AgentDojo v1.2.2(`agentdojo==0.1.35`)다. 문서는 `docs/architecture.md`(구조·집행 지점), `docs/rulebook.md`(룰북·판정 입력), `docs/adapter_guide.md`(Adapter 부착 위치), `docs/integration_guide.md`(contract v1), 결과는 `results/RESULTS.md`다.

## 규칙

- agentdojo 패키지 소스는 수정하지 않는다. 확장은 서브클래스·래핑으로만 한다.
- 판정 코드(`src/agentgate/core/`, `policy_gate/rule_checker.py`·`rule_facts.py`·`toolspec_schema.py`)에는 LLM 호출을 넣지 않는다. LLM 호출은 TaskSpec 추출기(`llm/`), 판정 LLM(`policy_gate/llm_judge.py`), ToolSpec 추출기(`policy_gate/toolspec.py`), 평가 대상 에이전트(`agents/`)에만 둔다.
- TaskSpec 추출기의 입력은 사용자 프롬프트와 툴 목록(이름·설명)뿐이다. 툴 응답, ground truth, injection GOAL을 넣지 않는다.
- 모든 툴 실행은 `GatedRuntime.run_function`을 지난다. Adapter는 툴 본문을 `bridge.invoke`(`harness/tool_schema.gated_callable`)로 두고 `routes_tools_through_bridge = True`를 선언하며 conformance를 통과해야 한다. Gate 밖 실행이 있는 run은 무효다.
- `prompts/*.md`는 LLM 입력이며 캐시 키에 들어간다. 바꾸면 캐시가 무효가 되므로 이유 없이 고치지 않는다.
- 정책(룰북·정적 규칙·판정 LLM)을 수정하면 pytest 전체와 `scripts/evaluation/replay_evaluation.py`를 다시 돌려 이전 결과와 비교한다.
- 평가 수치를 바꾸려고 정책을 조정할 때는 먼저 변경안을 보고하고 승인을 받는다.
- 문서와 보고서의 수치는 스크립트 출력에서만 가져온다. 확인하지 못한 사실은 "확인되지 않음"으로 쓴다.
- 문서는 한국어 문어체로 핵심만 쓰고, 기술 용어·식별자·논문명은 영어로 둔다. 새 문서를 늘리기보다 `docs/` 네 문서와 README를 갱신한다. README는 핵심만 짧게 둔다.
- Python 파일은 첫 줄이 한국어 한 줄 요약인 module docstring으로 시작한다.

## 저장소 운영

- API 키는 `.env`(git 제외)에만 둔다. 형식은 `.env.example`이다.
- `results/RESULTS.md`는 손으로 고치지 않고 `scripts/analysis/summarize_results.py`·`scripts/evaluation/replay_contrast.py`를 고쳐 재생성한다.
- `config/agentdojo/named_source_rules.yaml`의 `trusted_tools`에 툴을 추가하면 `tests/test_config_consistency.py`로 인젝션 벡터가 없음을 확인한다.
- AgentDojo 고유 값(툴 이름 등)은 `config/agentdojo/`, `src/agentgate/environments/agentdojo.py`, `src/agentgate/scenarios/`에만 둔다. Gate core·정책 코드는 `ToolBinding`(`core/binding.py`)으로만 읽는다.
- LLM 캐시는 `data/cache/{taskspec,toolspec,judge}/`(git 제외)다. 조건 비교는 같은 실행 안의 `no_gate`와 한다.
- 오래 걸리는 평가(재생 전체, live)는 백그라운드로 돌리고 로그를 `logs/`(git 제외)에 남긴다.
- 클라우드 세션은 SessionStart hook(`scripts/cloud/setup.sh`)이 `.venv`를 준비한다. 시작하면 `python scripts/cloud/check_env.py`부터 돌리고 실패 항목을 먼저 보고한다.

## 구조

- `src/agentgate/`: `core/`(`GatedRuntime`, `binding`(ToolBinding), `trace`), `policy_gate/`(`PolicyGateRuntime`, `judge`(Judge 등록부), `rule_checker`·`rule_facts`, `llm_judge`, `toolspec`·`toolspec_schema`), `environments/`(`Environment` 등록부, AgentDojo), `adapters/`, `harness/`(Bridge·Session·registry·runner·contract 서비스·conformance), `experiment/`(`conditions`·`batch`·`cli`·`metrics`·`live_run_analysis`), `scenarios/`(AgentDojo suite·pipeline·재생, 옛 이름 shim), `agents/`, `llm/`.
- 설정: `config/gate.yaml`, `config/rulebook.yaml`, `config/frameworks.yaml`, `config/agentdojo/`. 판정 방식 분기는 공통 Gate에 넣지 않고 등록부(`register_judge`, `register_condition`)·hook·서브클래스로 둔다.

## 자주 쓰는 명령

```bash
. .venv/bin/activate
python -m pytest -q
ruff check src scripts tests dashboard
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
python -m agentgate.experiment.cli --framework smolagents --condition gate_static_rules --model ollama:gpt-oss:120b --pilot --out results/runs/<run>
python -m agentgate.harness.conformance --framework <name> --conditions no_gate gate_static_rules
python scripts/evaluation/replay_evaluation.py --condition gate_static_rules --spec oracle --out results/replay/oracle
python scripts/analysis/check_enforcement.py --run results/runs/<run> && python scripts/analysis/summarize_live.py --run results/runs/<run>
python scripts/analysis/inspect_trace.py pair banking user_task_0 injection_task_0 --mode enforce
```
