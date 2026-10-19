"""레이블 일관성, 인자 출처(provenance), 공격자 목적지를 검사한다.

LLM 호출 없음.
입력: data/agentdojo/benchmark/{user_tasks,injection_tasks}.json, injection_labels.yaml (extract_benchmark.py 선행)
출력(data/agentdojo/benchmark/): label_check.json, arg_provenance.json, provenance_summary.json, attacker_dest_check.json

사용법: python scripts/agentdojo/analyze_provenance.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import yaml

from agentgate import SUITES, v1
from agentgate.agentdojo.live_run_analysis import ATTACKER_DESTINATIONS
from agentgate.agentdojo.suites import load_default_environment, load_suite, load_task_environment
from agentgate.config import PROJECT_ROOT
from agentgate.gate.engine import AllowAllPolicyEngine
from agentgate.gate.normalization import build_haystack, haystack_contains, iterate_leaf_values, normalize_value
from agentgate.gate.runtime import GatedRuntime
from agentgate.gate.task_spec import TaskSpec

BENCHMARK_DIR = PROJECT_ROOT / "data" / "agentdojo" / "benchmark"

# Injection type -> tool families that must appear among the injection task's calls.
TYPE_TOOLS = {
    "A": {"send_email", "send_direct_message", "send_channel_message", "post_webpage"},
    "B": {"send_money", "schedule_transaction", "update_scheduled_transaction"},
    "C": {"send_email", "send_direct_message", "send_channel_message", "post_webpage", "send_money"},
    "D": {"delete_email", "delete_file"},
    "E": {"update_password", "update_user_info", "invite_user_to_slack", "remove_user_from_slack",
          "add_user_to_channel", "share_file"},
    "F": {"send_email", "send_direct_message", "send_channel_message", "get_webpage"},
    "G": {"reserve_hotel", "reserve_car_rental", "reserve_restaurant", "create_calendar_event"},
    "H": set(),
}
POLICY_TOOLS = {
    "P1": set(v1.load_config()["P1"]["targets"]),
    "P2": set(v1.load_config()["external_sinks"]),
    "P3": set(v1.load_config()["external_sinks"]),
    "P4": set(v1.load_config()["P4"]["tools"]),
    "P5": {"delete_email", "delete_file"},
    "P6": set(v1.load_config()["P6"]["tools"]),
    "P7": set(v1.load_config()["P7"]["message_tools"]) | set(v1.load_config()["P7"]["visit_tools"]),
    "P8": set(v1.load_config()["P8"]["tools"]),
    "P9": set(v1.load_config()["P9"]["tools"]),
}


def target_args() -> dict[str, list[str]]:
    policy_config = v1.load_config()
    args_by_tool: dict[str, list[str]] = defaultdict(list)
    for tool, args in policy_config["P1"]["targets"].items():
        args_by_tool[tool] += args
    for tool, arg in policy_config["P4"]["tools"].items():
        args_by_tool[tool].append(arg)
    args_by_tool["delete_file"].append("file_id")
    args_by_tool["delete_email"].append("email_id")
    args_by_tool["update_password"].append("password")
    args_by_tool["get_webpage"].append("url")
    for tool, arg in policy_config["P8"]["tools"].items():
        args_by_tool[tool].append(arg)
    args_by_tool["send_direct_message"].append("recipient")
    args_by_tool["invite_user_to_slack"].append("user")
    return {tool: sorted(set(args)) for tool, args in args_by_tool.items()}


def label_check() -> list[dict]:
    labels = yaml.safe_load((BENCHMARK_DIR / "injection_labels.yaml").read_text())
    injection_tasks = json.loads((BENCHMARK_DIR / "injection_tasks.json").read_text())
    problems = []
    for injection_task in injection_tasks:
        label = labels[injection_task["suite"]][injection_task["task_id"]]
        tools = {c["function"] for c in injection_task["ground_truth"]}
        task_key = _task_key(injection_task)
        if label["type"] == "H":
            if tools:
                problems.append({**task_key, "issue": "type H but has tool calls", "tools": sorted(tools)})
            continue
        if not tools & TYPE_TOOLS[label["type"]]:
            problems.append({**task_key, "issue": f"type {label['type']} but no matching tool", "tools": sorted(tools)})
        for policy_id in label["policies"]:
            if not tools & POLICY_TOOLS[policy_id]:
                problems.append({**task_key, "issue": f"target policy {policy_id} has no applicable call",
                                 "tools": sorted(tools)})
        if not any(c["role"] == "effect" for c in injection_task["ground_truth"]):
            problems.append({**task_key, "issue": "no effect call", "tools": sorted(tools)})
    return problems


def _task_key(task: dict) -> dict:
    return {"suite": task["suite"], "task_id": task["task_id"]}


def provenance() -> tuple[list[dict], dict]:
    targets = target_args()
    rows = []
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        for task_id, user_task in suite.user_tasks.items():
            env = load_task_environment(suite, user_task)
            runtime = GatedRuntime(suite.tools, prompt=user_task.PROMPT, spec=TaskSpec(),
                                   engine=AllowAllPolicyEngine(), mode="audit")
            for call in user_task.ground_truth(env.model_copy(deep=True)):
                if call.function in targets:
                    for arg in targets[call.function]:
                        for value in iterate_leaf_values(call.args.get(arg)):
                            occurrences = runtime.index.find_occurrences(value)
                            if any(o.source_id == "prompt" for o in occurrences):
                                category, first_tool = "PROMPT", None
                            elif occurrences:
                                category = "TOOL"
                                first_tool = {"step": occurrences[0].step, "tool": occurrences[0].tool}
                            else:
                                category, first_tool = "UNSEEN", None
                            rows.append({"suite": suite_name, "task_id": task_id, "step": len(runtime.trace),
                                         "tool": call.function, "arg": arg, "value": value,
                                         "normalized": normalize_value(value),
                                         "category": category, "first_tool": first_tool})
                runtime.run_function(env, call.function, call.args)
    summary: dict = defaultdict(Counter)
    for row in rows:
        summary[f"{row['tool']}.{row['arg']}"][row["category"]] += 1
    return rows, {key: dict(counts) for key, counts in sorted(summary.items())}


def attacker_dest_check() -> dict:
    user_tasks = json.loads((BENCHMARK_DIR / "user_tasks.json").read_text())
    result = {}
    env_haystacks = {}
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        env_haystacks[suite_name] = build_haystack(load_default_environment(suite).model_dump_json())
    for destination in ATTACKER_DESTINATIONS:
        in_prompt = [f"{u['suite']}/{u['task_id']}" for u in user_tasks
                     if haystack_contains(build_haystack(u["PROMPT"]), destination)]
        in_env = [suite_name for suite_name, haystack in env_haystacks.items()
                  if haystack_contains(haystack, destination)]
        result[destination] = {"in_user_prompts": in_prompt, "in_default_env": in_env,
                               "ok": not in_prompt and not in_env}
    return result


def main():
    label_problems = label_check()
    (BENCHMARK_DIR / "label_check.json").write_text(json.dumps(label_problems, indent=1))
    rows, summary = provenance()
    (BENCHMARK_DIR / "arg_provenance.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str))
    (BENCHMARK_DIR / "provenance_summary.json").write_text(json.dumps(summary, indent=1))
    destination_check = attacker_dest_check()
    (BENCHMARK_DIR / "attacker_dest_check.json").write_text(json.dumps(destination_check, indent=1))
    print("label problems:", len(label_problems))
    for problem in label_problems:
        print("  ", problem)
    print("provenance (tool.arg: PROMPT / TOOL / UNSEEN):")
    for key, counts in summary.items():
        print(f"  {key:45s} {counts.get('PROMPT', 0):3d} {counts.get('TOOL', 0):3d} {counts.get('UNSEEN', 0):3d}")
    print("attacker destinations ok:", sum(v["ok"] for v in destination_check.values()), "/", len(destination_check))
    for destination, check in destination_check.items():
        if not check["ok"]:
            print("  ", destination, check)


if __name__ == "__main__":
    main()
