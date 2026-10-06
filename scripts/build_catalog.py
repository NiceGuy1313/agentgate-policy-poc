"""Stage 1 / task 1+3: extract the task catalog from AgentDojo (no LLM calls).

Outputs:
  data/catalog/user_tasks.json
  data/catalog/injection_tasks.json   (ground truth calls carry a `role` field: prep | effect)
  data/catalog/tools.json             (suite tool list: name, description, parameters)
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agentgate import BENCHMARK_VERSION, SUITES
from agentgate.attack_trajectories import attack_calls
from agentgate.suites import default_env, injection_candidates, load_suite

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "catalog"


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "model_dump"):
        return _jsonable(v.model_dump())
    return v


def calls_of(task, env) -> list[dict]:
    return [{"function": c.function, "args": _jsonable(dict(c.args))} for c in task.ground_truth(env.model_copy(deep=True))]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    roles = yaml.safe_load((ROOT / "config" / "tool_roles.yaml").read_text())
    effect_tools = {t for tools in roles["effect"].values() for t in tools}
    users, injs, tools = [], [], {}
    for s in SUITES:
        suite = load_suite(s)
        tools[s] = [
            {"name": f.name, "description": f.description, "parameters": list(f.parameters.model_json_schema().get("properties", {}))}
            for f in suite.tools
        ]
        for tid, ut in sorted(suite.user_tasks.items(), key=lambda kv: int(kv[0].split("_")[-1])):
            env = ut.init_environment(default_env(suite))
            users.append({
                "suite": s, "task_id": tid, "PROMPT": ut.PROMPT,
                "ground_truth": calls_of(ut, env),
                "injection_vectors": list(injection_candidates(s, tid)),
            })
        for tid, it in sorted(suite.injection_tasks.items(), key=lambda kv: int(kv[0].split("_")[-1])):
            env = default_env(suite)
            calls, source = attack_calls(s, it, env)
            gt = [{"function": c.function, "args": _jsonable(dict(c.args))} for c in calls]
            for c in gt:
                c["role"] = "effect" if c["function"] in effect_tools else "prep"
            injs.append({"suite": s, "task_id": tid, "GOAL": it.GOAL, "gt_source": source, "ground_truth": gt})
    (OUT / "user_tasks.json").write_text(json.dumps(users, indent=1, ensure_ascii=False))
    (OUT / "injection_tasks.json").write_text(json.dumps(injs, indent=1, ensure_ascii=False))
    (OUT / "tools.json").write_text(json.dumps(tools, indent=1, ensure_ascii=False))
    print(f"benchmark {BENCHMARK_VERSION}: user_tasks={len(users)} injection_tasks={len(injs)}")
    for s in SUITES:
        print(f"  {s}: user={sum(u['suite']==s for u in users)} injection={sum(i['suite']==s for i in injs)}")


if __name__ == "__main__":
    main()
