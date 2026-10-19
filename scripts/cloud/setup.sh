#!/bin/bash
# Claude Code 클라우드 세션 준비. .claude/settings.json의 SessionStart hook이 부른다. 로컬에서는 아무것도 하지 않는다.
# 여러 번 실행해도 된다: 이미 설치된 단계는 건너뛴다. 결과 확인은 scripts/cloud/check_env.py.
set -u

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${FORCE_CLOUD_SETUP:-}" != "1" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" || exit 0
LOG="$ROOT/logs/cloud_setup.log"
mkdir -p "$ROOT/logs"
exec >>"$LOG" 2>&1
echo "=== cloud setup $(date -u +%FT%TZ) ==="

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
PY=.venv/bin/python
$PY -m pip install -q --upgrade pip

# 1) agentgate + AgentDojo (PyPI·GitHub: Trusted 기본 허용 목록으로 충분)
if ! $PY -c "import agentgate, agentdojo, fastapi" 2>/dev/null; then
  $PY -m pip install -q -e '.[dev,dashboard]' || echo "WARN: base install failed"
fi

# 2) 이후 Bash 명령에서 venv를 쓰도록 세션 환경에 남긴다
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PATH=\"$ROOT/.venv/bin:\$PATH\""
    echo "export VIRTUAL_ENV=\"$ROOT/.venv\""
  } >>"$CLAUDE_ENV_FILE"
fi
echo "=== done ==="
exit 0
