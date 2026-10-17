"""AgentDojo에서 태스크와 툴 목록을 추출하고, 정답 호출에 config/tool_roles.yaml의 역할(prep | effect)을 표시한다. LLM 호출 없음.

출력:
  data/agentdojo/benchmark/user_tasks.json
  data/agentdojo/benchmark/injection_tasks.json   (ground truth 호출에 `role` 필드: prep | effect)
  data/agentdojo/benchmark/tools.json             (suite 툴 목록: name, description, parameters)

사용법: python scripts/agentdojo/extract_benchmark.py
"""

from __future__ import annotations

import json

import yaml

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.agentdojo.attack_trajectories import plan_attack_calls
from agentgate.agentdojo.suites import find_injection_candidates, load_default_environment, load_suite, task_number
from agentgate.config import PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "data" / "agentdojo" / "benchmark"


def _to_jsonable(value):
    if isinstance(value, dict):
        return {k: _to_jsonable(x) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(x) for x in value]
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    return value


def ground_truth_calls(task, env) -> list[dict]:
    return [{"function": c.function, "args": _to_jsonable(dict(c.args))}
            for c in task.ground_truth(env.model_copy(deep=True))]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    roles = yaml.safe_load((PROJECT_ROOT / "config" / "agentdojo" / "tool_roles.yaml").read_text())
    effect_tools = {tool for tools in roles["effect"].values() for tool in tools}
    user_tasks, injection_tasks, tools_by_suite = [], [], {}
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        tools_by_suite[suite_name] = [
            {"name": f.name, "description": f.description,
             "parameters": list(f.parameters.model_json_schema().get("properties", {}))}
            for f in suite.tools
        ]
        for task_id, user_task in sorted(suite.user_tasks.items(), key=lambda item: task_number(item[0])):
            env = user_task.init_environment(load_default_environment(suite))
            user_tasks.append({
                "suite": suite_name, "task_id": task_id, "PROMPT": user_task.PROMPT,
                "ground_truth": ground_truth_calls(user_task, env),
                "injection_vectors": list(find_injection_candidates(suite_name, task_id)),
            })
        for task_id, injection_task in sorted(suite.injection_tasks.items(), key=lambda item: task_number(item[0])):
            env = load_default_environment(suite)
            calls, source = plan_attack_calls(suite_name, injection_task, env)
            ground_truth = [{"function": c.function, "args": _to_jsonable(dict(c.args))} for c in calls]
            for call in ground_truth:
                call["role"] = "effect" if call["function"] in effect_tools else "prep"
            injection_tasks.append({"suite": suite_name, "task_id": task_id, "GOAL": injection_task.GOAL,
                                    "gt_source": source, "ground_truth": ground_truth})
    (OUTPUT_DIR / "user_tasks.json").write_text(json.dumps(user_tasks, indent=1, ensure_ascii=False))
    (OUTPUT_DIR / "injection_tasks.json").write_text(json.dumps(injection_tasks, indent=1, ensure_ascii=False))
    (OUTPUT_DIR / "tools.json").write_text(json.dumps(tools_by_suite, indent=1, ensure_ascii=False))
    print(f"benchmark {BENCHMARK_VERSION}: user_tasks={len(user_tasks)} injection_tasks={len(injection_tasks)}")
    for suite_name in SUITES:
        user_count = sum(u["suite"] == suite_name for u in user_tasks)
        injection_count = sum(i["suite"] == suite_name for i in injection_tasks)
        print(f"  {suite_name}: user={user_count} injection={injection_count}")


if __name__ == "__main__":
    main()
