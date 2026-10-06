#!/usr/bin/env bash
# Probe which Ollama Cloud models act on an injection (C0, no defense) with one attack on 4 fixed pairs.
#   bash scripts/probe_models.sh tool_knowledge "model1 model2 ..."
set -euo pipefail
ATTACK=${1:-tool_knowledge}
MODELS=${2:?model list}
PAIRS="slack:user_task_1:injection_task_1,slack:user_task_1:injection_task_3,banking:user_task_0:injection_task_3,banking:user_task_0:injection_task_7"
OUT=results/live/v1/probe_${ATTACK}
for m in $MODELS; do
  echo "$m"
done | xargs -P 4 -I{} bash -c '
  m="{}"; d="'"$OUT"'/$(echo "$m" | tr ":/" "__")"
  AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL="$m" .venv/bin/python scripts/live_eval.py --agent llm \
    --spec llm --conditions C0 --attack '"$ATTACK"' --pairs "'"$PAIRS"'" --workers 4 --out "$d" > "$d.log" 2>&1 \
    || echo "FAILED $m"; echo "done $m"'
