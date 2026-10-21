#!/bin/bash
# 프레임워크 요인 분리 실험(Gate 없음)을 팔 단위로 차례로 돌린다. 끝난 run은 건너뛰므로 멈췄다가 이어서 돌릴 수 있다.
#
# 사용법: bash scripts/evaluation/factor_runs.sh start|stop|status
#   start  : 백그라운드로 시작(이미 끝난 run은 건너뜀). 진행 로그 logs/f_progress.log
#   stop   : 실행 중인 run을 멈춘다(진행 중이던 run만 버려지고, 끝난 기록은 남는다)
#   status : 팔별 완료 수
# 환경 변수: WORKERS(기본 3, Ollama 동시 요청 한도), PAIRS(기본 사용자 작업마다 공격 하나인 96건)
set -u
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
PAIRS=${PAIRS:-data/agentdojo/pairs/role_ablation_97.txt}
WORKERS=${WORKERS:-3}
PIDFILE=logs/f_runner.pid
ARMS=(
  "s_default|smolagents|{}"
  "s_tool|smolagents|{\"tool_response_role\":\"tool\"}"
  "s_min|smolagents|{\"system_prompt\":\"minimal\"}"
  "s_min_tool|smolagents|{\"system_prompt\":\"minimal\",\"tool_response_role\":\"tool\"}"
  "s_no_persona|smolagents|{\"system_prompt\":\"no_persona\"}"
  "s_no_chaining|smolagents|{\"system_prompt\":\"no_chaining\"}"
  "s_no_examples|smolagents|{\"system_prompt\":\"no_examples\"}"
  "s_no_always|smolagents|{\"system_prompt\":\"no_always_rule\"}"
  "s_min_tool_shell|smolagents|{\"system_prompt\":\"minimal\",\"tool_response_role\":\"tool\",\"observation_format\":\"shell\"}"
  "mini|mini-swe-agent|{}"
  "native|native|{}"
)

run_all() {
  for arm in "${ARMS[@]}"; do
    IFS='|' read -r name fw opts <<< "$arm"
    for attempt in 1 2 3; do
      $PY -m agentgate.experiment.cli --framework "$fw" --condition no_gate --model ollama:gpt-oss:120b \
        --attack tool_knowledge --no-benign --pairs "@$PAIRS" --framework-options "$opts" --workers "$WORKERS" \
        --out "results/runs/f_$name" >> "logs/f_$name.log" 2>&1
      # Ollama 서버 오류(429·500)로 실패한 기록은 지우고 다시 돌린다
      n=$($PY -c "
import glob, json, os
bad = [f for f in glob.glob('results/runs/f_$name/no_gate/*/*/*.json') if json.load(open(f)).get('error')]
[os.remove(f) for f in bad]
print(len(bad))")
      echo "$(date +%H:%M) $name attempt $attempt errors_removed=$n" >> logs/f_progress.log
      [ "$n" = "0" ] && break
    done
    echo "$(date +%H:%M) $name DONE" >> logs/f_progress.log
  done
  echo "$(date +%H:%M) ALL DONE" >> logs/f_progress.log
  rm -f "$PIDFILE"
}

case "${1:-status}" in
  start)
    mkdir -p logs
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "already running"; exit 0; fi
    nohup bash "$0" _run > logs/f_runner.log 2>&1 &
    echo $! > "$PIDFILE"; echo "started (pid $!)";;
  _run) run_all;;
  stop)
    [ -f "$PIDFILE" ] && pkill -P "$(cat "$PIDFILE")" 2>/dev/null; [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null
    pkill -f "agentgate.experiment.cli.*results/runs/f_" 2>/dev/null
    rm -f "$PIDFILE"; echo "$(date +%H:%M) STOPPED" >> logs/f_progress.log; echo "stopped";;
  status)
    total=$(wc -l < "$PAIRS" | tr -d ' ')
    for arm in "${ARMS[@]}"; do
      name=${arm%%|*}
      done_n=$($PY -c "
import glob
keys = {l.strip() for l in open('$PAIRS') if l.strip()}
print(sum(1 for f in glob.glob('results/runs/f_$name/no_gate/*/*/*.json')
          if ':'.join(f.split('/')[-3:]).removesuffix('.json') in keys))")
      echo "$name $done_n/$total"
    done
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo "running" || echo "not running";;
esac
