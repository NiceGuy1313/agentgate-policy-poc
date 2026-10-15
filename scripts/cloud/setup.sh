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

# 2) 판별기 의존성. CPU torch는 download.pytorch.org가 허용 목록에 있어야 한다(명세서.md 1.2). 없으면 PyPI로 대체
if ! $PY -c "import torch, transformers" 2>/dev/null; then
  $PY -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu \
    || { echo "WARN: CPU torch index unreachable, falling back to PyPI torch"; $PY -m pip install -q torch; }
  $PY -m pip install -q -e '.[detector]' || echo "WARN: detector extra install failed"
fi

# 3) 판별기 모델 미리 받기(huggingface.co 허용 필요). 실패해도 세션은 시작한다
export HF_HOME="${HF_HOME:-$ROOT/.hf_cache}"
$PY - <<'EOF' || echo "WARN: model download failed (huggingface.co allowed?)"
import yaml
from huggingface_hub import snapshot_download
for name, model in yaml.safe_load(open("config/detector.yaml"))["models"].items():
    print(name, snapshot_download(model["hf_id"], revision=model.get("revision")))
EOF

# 4) 이후 Bash 명령에서 venv와 HF 캐시를 쓰도록 세션 환경에 남긴다
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PATH=\"$ROOT/.venv/bin:\$PATH\""
    echo "export VIRTUAL_ENV=\"$ROOT/.venv\""
    echo "export HF_HOME=\"$HF_HOME\""
  } >>"$CLAUDE_ENV_FILE"
fi
echo "=== done ==="
exit 0
