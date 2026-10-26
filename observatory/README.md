# Agent Observatory

에이전트(mini-swe-agent, smolagents, browser-use, …) 저장소 루트에 이 `observatory/` 폴더를 넣고 명령 하나를 실행하면, Claude Code가 그 에이전트를 로거에 연결하고 정적 검증기가 빠진 곳이 없는지 확인합니다. dashboard는 실행 기록과 함께, 코드에서 읽어 낸 에이전트 루프를 왼쪽에 그립니다. dashboard, 로거, 검증기는 어떤 에이전트에서도 똑같습니다.

## 구성

| 파일 | 역할 | 에이전트마다 |
|---|---|---|
| `observatory_log.py` | 로거. 에이전트 코드 옆에 복사해 호출만 심는다. 표준 라이브러리만 사용 | 같음 |
| `flow_verify.py` | 정적 검증기. 기준점에서 값의 흐름을 따라가 로그가 통째로 기록하는지 판정하고, 에이전트 루프 그림(`verify/loop.mmd`)을 그린다 | 같음 |
| `dashboard.py`, `static/index.html` | dashboard 서버와 화면. 왼쪽은 `verify/loop.mmd`의 에이전트 루프, 오른쪽은 실행 기록 | 같음 |
| `run.py`, `prompt/*.md`, `selftest.py` | 실행 스크립트, 단계별 Claude 요청, Observatory 자체 테스트 | 같음 |
| `litellm.yaml`, `.env.example` | 다른 LLM으로 실행할 때의 프록시 설정, API 키 양식 | 같음 |
| `candidates.py` | `--scope`로 고른 파일에서 기준점이 될 수 있는 호출(구현이 보이지 않는 호출)을 모두 뽑아 1단계에 넘긴다 | 같음 |
| `diagram/verify-anchors.json` | 검증기의 기준점 (진입 함수, LLM 응답과 사용자 입력이 들어오는 호출) | **Claude가 작성** |
| 에이전트 코드 안의 로거 호출 | LLM 호출, tool 실행, 사용자 입력 위치에 심는 호출 | **Claude가 추가** |

## 동작 방식

```
에이전트 ──observatory_log 호출──▶ observatory/runs/*.jsonl ──▶ dashboard.py ──SSE──▶ 브라우저
(이벤트마다 호출 위치 at=파일:줄)  (OBSERVATORY_RUNS를 켰을 때만 기록)        (왼쪽: verify/loop.mmd의 루프)
```

로거는 `OBSERVATORY_RUNS` 환경변수가 설정되어 있을 때만 기록합니다. 설정하지 않고 실행하면 로그 호출이 들어간 뒤에도 에이전트는 아무것도 기록하지 않고 전과 똑같이 동작합니다.

`run.py`는 단계마다 Claude Code 세션을 하나씩 엽니다. 검증기는 각 단계에 로그를 넣을 위치를 알려 주고, 마지막에 결과를 독립적으로 판정합니다. 판정 결과는 세션에 돌려주지 않습니다: 모델이 검증기 출력에 맞춰 코드를 바꾸면 검증의 의미가 없어지기 때문입니다.

| 단계 | Claude가 하는 일 | Observatory가 하는 일 |
|---|---|---|
| 1 | 기준점 작성 | 로거를 진입 함수 옆에 복사, 로그를 넣기 전의 값 흐름 기록 |
| 2, 3, 4 | LLM, tool, 사용자 입력 로그 추가 | 검증기가 찾은 호출 위치를 각 단계에 전달 |
| - | | 판정 (아래), 루프 그림 작성 |


dashboard 왼쪽 그림은 검증기가 코드에서 결정적으로 그립니다(모델은 그리지 않습니다). 진입 함수, 로그가 있는 함수, 밖으로 나가는 호출(LLM, tool, 사용자 입력), 상태만 남기고 그 사이의 보조 함수는 접습니다. 로그가 있는 함수는 박스의 이름 아래에 로그 호출마다 `[LOG] <이벤트> @ <파일:줄>`을 한 줄씩 적습니다. 박스를 누르면 VS Code에서 그 코드가 열리고, 기록의 행을 누르면 그 이벤트를 남긴 박스가 강조됩니다.

## 실행 전에 준비할 것

| 준비물 | 버전 (확인한 버전) | 설명 |
|---|---|---|
| `uv` | 0.3 이상 (0.12.9) | Observatory의 스크립트를 `uv run`으로 실행합니다. Python 3.10 이상은 uv가 알아서 받습니다. 검사에 쓰는 `ruff`도 `uvx`로 자동으로 받습니다. |
| `git` | (2.52.0) | 에이전트 폴더가 **git 저장소여야 합니다.** Observatory는 `git ls-files`로 에이전트 파일을 찾기 때문에, git 저장소가 아니면 검사할 파일이 0개가 됩니다. 처음이면 `git init && git add -A && git commit -m base`로 원래 상태를 남겨 두세요. 끝나고 `git diff`로 바뀐 것을 볼 수 있습니다. |
| **Claude Code** | (2.1.128) | `claude` CLI가 PATH에 있고 로그인되어 있어야 합니다 (아니면 `CLAUDE_BIN`에 경로). 이 계정으로 실행되고 비용이 듭니다. |


## API 키 (`.env`)

Observatory 자체는 키가 필요 없습니다. 로그 연결은 로그인된 Claude Code로 합니다. 키가 필요한 것은 **연결한 에이전트를 실제로 실행할 때** 그 에이전트가 쓰는 모델 키입니다.

```bash
cp observatory/.env.example observatory/.env     # 에이전트가 쓰는 키만 채웁니다
set -a; source observatory/.env; set +a          # 에이전트를 실행할 터미널에서
```

- `observatory/.env`는 `observatory/.gitignore`에 들어 있어 커밋되지 않습니다.
- Observatory는 `.env`를 자동으로 읽지 않습니다. 위처럼 실행 전에 불러옵니다.
- `ANTHROPIC_API_KEY`를 불러온 터미널에서 `run.py`를 실행하면, Claude Code가 로그인 대신 이 API 키로 과금됩니다. 로그인으로 쓰려면 `run.py`는 키를 불러오지 않은 터미널에서 실행하세요.
- 로거는 이름에 `KEY`, `TOKEN`, `SECRET`, `PASSWORD`가 들어간 환경변수 값을 실행 기록에서 지웁니다.

## 실행

**1. 로그 연결과 판정.** 에이전트 저장소 루트에서, `--scope`에 에이전트의 실행 루프, 모델 호출, 사용자 입력이 들어 있는 파일을 적습니다.

```bash
uv run observatory/run.py --scope agent.py llm.py tools.py
```

**2. 실행 기록 보기.** 터미널 두 개를 씁니다.

```bash
# 터미널 1: dashboard (http://127.0.0.1:8001)
uv run observatory/dashboard.py

# 터미널 2: .env의 키를 불러오고, OBSERVATORY_RUNS를 설정해 에이전트를 실행
set -a; source observatory/.env; set +a
OBSERVATORY_RUNS=$PWD/observatory/runs <에이전트 실행 명령>
```

`<에이전트 실행 명령>`은 그 에이전트를 실행하는 명령입니다. 예를 들어 mini-swe-agent는 `uv run mini -m <모델> -t "<작업>"`입니다. 실행이 시작되면 dashboard 오른쪽에 기록이 실시간으로 올라옵니다.

세션들은 합쳐서 수십 분 걸릴 수 있습니다. 끝나면 `git diff`로 Claude가 바꾼 것을 검토하세요. 커밋은 Observatory가 하지 않습니다.

## 다른 LLM으로 실행

로그 연결은 Claude Code가 하지만, Claude Code가 부르는 모델은 다른 LLM으로 바꿀 수 있습니다. Claude Code는 Anthropic API 형식으로 요청하므로, 그 요청을 다른 모델로 넘겨 주는 [litellm](https://docs.litellm.ai/) 프록시를 로컬에 띄우고 Claude Code가 그 프록시를 쓰게 합니다.

```
run.py ──▶ Claude Code ──ANTHROPIC_BASE_URL──▶ litellm 프록시 (127.0.0.1:4004) ──▶ DeepInfra의 모델
```

`observatory/litellm.yaml`에 DeepInfra의 Qwen3.8-27B(`qwen`), Muse-Glimmer-30B(`glimmer`), Nemotron-3.5-Lightning(`nemotron`)이 들어 있습니다. 다른 모델은 같은 형식으로 `model_list`에 추가합니다.

```bash
# 터미널 1: 프록시. observatory/.env에 DEEPINFRA_API_KEY를 넣어 둡니다.
set -a; source observatory/.env; set +a
uvx --from 'litellm[proxy]' litellm --config observatory/litellm.yaml --host 127.0.0.1 --port 4004

# 터미널 2: 로그 연결. 세 군데의 qwen을 쓰려는 모델 이름(litellm.yaml의 model_name)으로 바꿉니다.
ANTHROPIC_BASE_URL=http://127.0.0.1:4004 ANTHROPIC_AUTH_TOKEN=sk-local-observatory ANTHROPIC_API_KEY= \
ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen ANTHROPIC_SMALL_FAST_MODEL=qwen CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
uv run observatory/run.py --model qwen --budget 0 --scope agent.py llm.py tools.py
```

- `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`: Claude Code가 Anthropic 대신 프록시에 요청하게 합니다. 토큰은 `litellm.yaml`의 `master_key`와 같아야 합니다.
- `ANTHROPIC_API_KEY=`: 비워서, 불러온 Anthropic 키 대신 위 토큰을 쓰게 합니다.
- `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`: Claude Code가 내부의 짧은 작업에 쓰는 보조 모델도 같은 모델로 돌립니다.
- `--budget 0`: Claude Code의 비용 계산은 Claude 모델 기준이라 의미가 없으므로 끕니다. 비용은 DeepInfra에서 확인합니다.
- 컨텍스트 창이 작은 모델(Muse-Glimmer-30B는 131k)은 smolagents처럼 큰 에이전트에서 컨텍스트 한도를 넘을 수 있습니다.

## 옵션

| 옵션 | 설명 |
|---|---|
| `--serve`, `--serve-only`, `--port` | dashboard를 실행(기본 8001). `--serve-only`는 다시 띄울 때 |
| `--no-claude` | Claude 세션 없이 판정과 루프 그림만 다시 (값 흐름과 지워진 줄 비교는 세션이 있을 때만) |
| `--model`, `--budget` | Claude 모델(기본 `sonnet`)과 세션당 최대 비용 USD(기본 3, 0이면 제한 없음) |
| `--scope <파일...>` | 에이전트를 이루는 파일. 1단계 세션은 이 파일만 읽고, 그 안에서 구현이 보이지 않는 호출 목록에서 기준점을 고른다 |
