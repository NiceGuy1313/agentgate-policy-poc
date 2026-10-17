"""v2 판별기를 AgentDojo 실제 툴 응답에 돌려 응답 단위 탐지율·오탐률을 잰다. 에이전트 LLM 없음.

suite마다 user task의 정답 호출을 (1) 공격 없는 환경, (2) 첫 injection task를 --attack으로 심은 환경에서 실행하고,
나온 툴 응답(에이전트가 받는 YAML 문자열, 중복 제거)을 판별기에 넣는다. 정답 라벨은 응답에 공격 표시
`<INFORMATION>`이 있는지다.

사용법: python scripts/v2/detector_tool_output_eval.py [--detector horizon-base] --out results/v2/detector/tool_outputs.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionsRuntime

from agentgate import SUITES
from agentgate.agentdojo.replay import ATTACK_MARKER, build_injections
from agentgate.agentdojo.suites import load_suite, task_number
from agentgate.v2.detector import load_detector_config, make_detector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--detector", default=None, help="model name in config/v2/detector.yaml (default: its default)")
    parser.add_argument("--attack", default="tool_knowledge")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    name = args.detector or load_detector_config()["default"]
    detector = make_detector(name)
    counts, misses, false_alarms, seen = Counter(), [], [], set()
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        runtime = FunctionsRuntime(suite.tools)
        injection_task = suite.get_injection_task_by_id(sorted(suite.injection_tasks, key=task_number)[0])
        for user_task_id in sorted(suite.user_tasks, key=task_number):
            user_task = suite.get_user_task_by_id(user_task_id)
            for injections in ({}, build_injections(suite_name, user_task, injection_task, args.attack)):
                env = user_task.init_environment(suite.load_and_inject_default_environment(injections))
                for call in user_task.ground_truth(env.model_copy(deep=True)):
                    output, error = runtime.run_function(env, call.function, call.args)
                    if error:
                        continue
                    text = tool_result_to_str(output)
                    if (suite_name, text) in seen:
                        continue
                    seen.add((suite_name, text))
                    injected = ATTACK_MARKER in text
                    result = detector.detect(text)
                    counts[(suite_name, injected, result.flagged)] += 1
                    case = {"suite": suite_name, "user_task": user_task_id, "tool": call.function,
                            "score": round(result.score, 4), "chars": len(text)}
                    if injected and not result.flagged:
                        misses.append(case)
                    if not injected and result.flagged:
                        false_alarms.append(case)

    def rates(keys):
        tp = sum(counts[(s, True, True)] for s in keys)
        positives = sum(counts[(s, True, f)] for s in keys for f in (True, False))
        fp = sum(counts[(s, False, True)] for s in keys)
        negatives = sum(counts[(s, False, f)] for s in keys for f in (True, False))
        return {"injected_flagged": tp, "injected": positives, "clean_flagged": fp, "clean": negatives}

    summary = {"detector": name, "model": load_detector_config()["models"][name], "attack": args.attack,
               "overall": rates(SUITES), "by_suite": {s: rates([s]) for s in SUITES},
               "misses": misses, "false_alarms": false_alarms}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    o = summary["overall"]
    print(f"{name}: injected {o['injected_flagged']}/{o['injected']} flagged, clean {o['clean_flagged']}/{o['clean']} flagged")


if __name__ == "__main__":
    main()
