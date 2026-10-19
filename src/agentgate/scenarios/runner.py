"""AgentDojo 시나리오 실행기: (조건, suite, 사용자 작업, injection task) 작업을 등록된 프레임워크로 실행한다.

CLI는 `python -m agentgate.scenarios.run`이다(scripts/evaluation/live_evaluation.py는 그 얇은 wrapper다). run마다
레코드 하나(<out>/<condition>/<suite>/<user_task>/<injection_task|none>.json)를 check_enforcement / summarize_live가
읽는 스키마로 쓰고, `summarize`가 <out>/scenario_summary.json을 쓴다. 정책 Gate를 우회한 툴 실행이 있는 run은
무효(`valid: false`)이고 점수는 요약에서 빠진다. LLM 캐시(TaskSpec, ToolSpec, 판정)는 data/cache/에 둔다.

  framework native   GatedRuntime 위의 AgentDojo pipeline(`run_live_task`), 기준 경로
  그 밖의 framework  agentgate.harness(`GateToolBridge` -> `Session.invoke` -> `PolicyGateRuntime.run_function`)
모든 프레임워크는 모든 조건(`agentgate.experiment.conditions`)에서 돈다.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import random
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate.agents.factory import UsageMeter, make_llm, spec_provider
from agentgate.agents.scripted_agent import ScriptedAgent
from agentgate.config import load_dotenv
from agentgate.experiment.conditions import CONDITIONS, get_condition, make_runtime
from agentgate.harness.session import SCORE_KEYS, invalidate_if_ungated
from agentgate.scenarios.pipeline import RecordingPipeline, build_pipeline, run_live_task
from agentgate.scenarios.replay import REPLAY_PIPELINE_NAME, get_cached_attack
from agentgate.scenarios.suites import load_suite, task_number

SUMMARY_FILE = "scenario_summary.json"   # summary.json is scripts/analysis/summarize_live.py's
PILOT = {"suites": ["banking"], "user_tasks": ["user_task_0", "user_task_2", "user_task_3", "user_task_13"],
         "injection_tasks": ["injection_task_0", "injection_task_4", "injection_task_7"]}


def record_path(out: str | Path, job: dict) -> Path:
    return (Path(out) / job["cond"] / job["suite"] / job["user_task"]
            / f"{job['injection_task'] or 'none'}.json")


def parse_pairs(pairs: str) -> list[tuple[str, str, str | None]]:
    """'suite:user_task:injection_task,...' or @file (one per line); injection_task 'none' = benign run."""
    items = Path(pairs[1:]).read_text().split() if pairs.startswith("@") else pairs.split(",")
    parsed = []
    for item in items:
        suite_name, user_task_id, injection_task_id = item.strip().split(":")
        parsed.append((suite_name, user_task_id, None if injection_task_id == "none" else injection_task_id))
    return parsed


def build_jobs(conditions: list[str], pairs: list[tuple[str, str, str | None]], **fields) -> list[dict]:
    """One job per (condition, pair). `fields`: agent, spec, attack, attack_model_name, on_block, out, framework,
    framework_options, model, max_steps."""
    for condition in conditions:
        get_condition(condition)
    return [{"cond": condition, "suite": suite_name, "user_task": user_task_id, "injection_task": injection_task_id,
             **fields}
            for condition in conditions for suite_name, user_task_id, injection_task_id in pairs]


def select_pairs(suites: list[str], user_tasks: list[str] | None, injection_tasks: list[str] | None, *,
                 benign: bool = True, sample: int = 0, seed: int = 0) -> list[tuple[str, str, str | None]]:
    """Pairs per suite: the given (or all, or `sample` drawn) user tasks x the given (or all) injection tasks,
    plus one benign run per user task when `benign`. An injection task 'none' in the list is the benign run."""
    pairs = []
    for suite_name in suites:
        suite = load_suite(suite_name)
        user_task_ids = user_tasks or sorted(suite.user_tasks, key=task_number)
        if sample:
            user_task_ids = sorted(random.Random(f"{seed}:{suite_name}").sample(user_task_ids, sample),
                                   key=task_number)
        injection_ids = sorted(suite.injection_tasks, key=task_number) if injection_tasks is None else injection_tasks
        with_benign = benign or "none" in injection_ids
        for user_task_id in user_task_ids:
            if with_benign:
                pairs.append((suite_name, user_task_id, None))
            pairs += [(suite_name, user_task_id, i) for i in injection_ids if i != "none"]
    return pairs


# ---------------------------------------------------------------------------------------------- one run
def run_framework_one(job: dict, output_path: Path) -> str:
    """One pair through agentgate.harness (any registered framework); same record schema as run_one."""
    from agentgate.harness.fake_llm import FakeLLMServer, ScriptedPlan
    from agentgate.harness.models import ModelSpec
    from agentgate.harness.runner import RunRequest, execute, run_scenario
    from agentgate.harness.session import ScenarioConfig, Session

    config = ScenarioConfig(job["suite"], job["user_task"], job["injection_task"], condition=job["cond"],
                            spec=job["spec"], attack=job["attack"], attack_model_name=job["attack_model_name"])
    provider, _, model_name = job["model"].partition(":")
    options = json.loads(job.get("framework_options") or "{}")
    try:
        if job["agent"] == "scripted":
            config.attack_model_name = "GPT-4"
            session = Session(config)
            with FakeLLMServer(ScriptedPlan(session)) as server:
                request = RunRequest(job["framework"], ModelSpec("openai_compat", "scripted", base_url=server.base_url),
                                     config, options, job.get("max_steps"))
                record = run_scenario(request, session=session)
        else:
            request = RunRequest(job["framework"], ModelSpec(provider, model_name), config, options,
                                 job.get("max_steps"))
            record = execute(request)
    except Exception as exc:  # keep going; record the failure
        record = {"condition": job["cond"], "suite": job["suite"], "user_task": job["user_task"],
                  "injection_task": job["injection_task"], "framework": job["framework"], "trace": [],
                  "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"}
    record["agent"] = job["agent"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, default=str))
    if record.get("valid") is False:
        print(f"INVALID {output_path}: {record.get('invalid_reason')}", flush=True)
        return "invalid"
    return "error" if record.get("error") else "ok"


_QUOTA_EXHAUSTED = threading.Event()


def reset_quota_flag() -> None:
    """사용량 한도 표시를 지운다. 앞선 실행에서 걸린 한도가 다음 실행(대시보드)을 막지 않게 한다."""
    _QUOTA_EXHAUSTED.clear()


def _is_quota_error(error: str | None) -> bool:
    """Provider usage limit (e.g. Ollama Cloud 'You used all included ... usage', HTTP 429): retrying cannot help."""
    return bool(error) and "429" in error and "usage" in error.lower()


def run_one(job: dict) -> str:
    load_dotenv()
    if _QUOTA_EXHAUSTED.is_set():
        return "quota"
    condition, suite_name = job["cond"], job["suite"]
    user_task_id, injection_task_id = job["user_task"], job["injection_task"]
    output_path = record_path(job["out"], job)
    if output_path.exists():
        return "skip"
    if job.get("framework", "native") != "native":
        return run_framework_one(job, output_path)
    suite = load_suite(suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)
    injection_task = suite.get_injection_task_by_id(injection_task_id) if injection_task_id else None
    meter = UsageMeter()
    if job["agent"] == "scripted":
        llm = ScriptedAgent(suite_name, user_task, injection_task, on_block=job["on_block"])
        attack_model = "GPT-4"
    else:
        llm = make_llm(meter)
        attack_model = job["attack_model_name"]
    cond = get_condition(condition)
    # every condition of a run (the no_gate baseline included) sees the same live-extracted spec
    provider = spec_provider(job["spec"], suite_name, user_task_id)
    stats: dict = {}
    runtime = make_runtime(cond, suite.tools, suite_name=suite_name, prompt=user_task.PROMPT, stats=stats,
                           record_env_hash=True)
    pipeline = build_pipeline(llm, provider)
    recording_pipeline = RecordingPipeline(pipeline)
    injections = {}
    if injection_task is not None:
        attack = get_cached_attack(suite_name, job["attack"], REPLAY_PIPELINE_NAME)
        attack.model_name = attack_model
        injections = attack.attack(user_task, injection_task)
    try:
        result = run_live_task(suite, recording_pipeline, user_task, injection_task, injections, runtime)
        error = None
    except Exception as exc:  # keep going; record the failure
        result, error = {}, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
    meter.collect()
    record = {
        "condition": condition, "suite": suite_name, "user_task": user_task_id, "injection_task": injection_task_id,
        "agent": job["agent"], "agent_model": getattr(llm, "name", None),
        "attack": job["attack"] if injection_task else None,
        "spec_source": job["spec"], "spec_meta": runtime.spec_meta,
        "spec": runtime.spec.model_dump() if runtime.spec is not None else None,
        "error": error, **result,
        "agent_tokens": {"input": meter.input_tokens, "output": meter.output_tokens, "calls": meter.calls,
                         "llm_ms": round(meter.ms, 1), "refusals": meter.refusals},
        "gate_ms": round(sum(e.decide_ms for e in runtime.trace), 3),
        "judge": runtime.judge_stats() if cond.judge else None,
        "toolspec": stats.get("toolspec") if cond.judge else None,
        "trace": runtime.trace_records(),
    }
    invalidate_if_ungated(record, list(runtime.ungated_executions))
    if _is_quota_error(error):
        # no record: the run did not happen, so a later invocation retries it instead of skipping it
        _QUOTA_EXHAUSTED.set()
        print(f"usage limit reached, stopping: {error.splitlines()[0][:200]}", flush=True)
        return "quota"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, default=str))
    if not record["valid"]:
        print(f"INVALID {output_path}: {record['invalid_reason']}", flush=True)
        return "invalid"
    return "error" if error else "ok"


def extract_specs_once(jobs: list[dict]) -> None:
    """One extraction per (suite, user task) before any run, so concurrent runs of the same task never extract it
    separately (they all read the same cached spec)."""
    from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
    from agentgate.scenarios.suites import describe_tools
    keys = sorted({(j["suite"], j["user_task"]) for j in jobs if j.get("framework", "native") == "native"})
    items = []
    for suite_name, user_task_id in keys:
        suite = load_suite(suite_name)
        items.append((suite.get_user_task_by_id(user_task_id).PROMPT,
                      [ToolInfo(**tool) for tool in describe_tools(suite)]))
    # one Message Batches request for every cache miss (anthropic): 50% price, prompt-cached tool list
    extractor = TaskSpecExtractor()
    results = extractor.extract_many(items)
    print(f"TaskSpec: {len(keys)} extracted ({sum(not r.cached for r in results)} live, "
          f"{sum(r.spec is None for r in results)} failed) -> {extractor.cache_dir}")


def run_jobs(jobs: list[dict], workers: int = 4) -> dict:
    """Run every job (completed records are skipped, so an interrupted run resumes). Returns status counts."""
    if any(j["spec"] == "llm" for j in jobs):
        extract_specs_once(jobs)
    # all runs of one user task (every condition, benign and attack) back to back: the judge's prompt-cache prefix
    # (system + TaskSpec) stays warm within its 5-minute TTL. Runs are independent, so the order does not change results.
    jobs = sorted(jobs, key=lambda j: (j["suite"], task_number(j["user_task"]), j["injection_task"] or "", j["cond"]))
    if jobs and all(j["agent"] == "scripted" and j.get("framework", "native") == "native" for j in jobs):
        with mp.Pool(workers) as pool:
            status = list(pool.imap_unordered(run_one, jobs, chunksize=8))
    else:
        with ThreadPoolExecutor(workers) as executor:
            status = list(executor.map(run_one, jobs))
    return dict(Counter(status))


# ---------------------------------------------------------------------------------------------- summary
def _mean(values):
    values = [float(v) for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def load_records(out: str | Path) -> list[dict]:
    """Per-run records under `out` (<condition>/<suite>/<user_task>/<injection_task>.json)."""
    return [json.loads(path.read_text()) for path in sorted(Path(out).glob("*/*/*/*.json"))
            if path.parts[-4] in CONDITIONS]


def summarize(records: list[dict]) -> dict:
    """Per condition: run counts and utility / ASR over valid, error-free runs. Invalid runs (gate bypass) are counted
    and listed, never scored."""
    by_condition = defaultdict(list)
    for record in records:
        by_condition[record["condition"]].append(record)
    conditions = {}
    for condition, runs in sorted(by_condition.items()):
        invalid = [r for r in runs if r.get("valid") is False]
        errors = [r for r in runs if r.get("error") and r.get("valid") is not False]
        scored = [r for r in runs if r.get("valid") is not False and not r.get("error")]
        benign = [r for r in scored if r["injection_task"] is None]
        attacks = [r for r in scored if r["injection_task"] is not None]
        conditions[condition] = {
            "runs": len(runs), "scored_runs": len(scored), "errors": len(errors), "invalid_runs": len(invalid),
            "benign_runs": len(benign), "attack_runs": len(attacks),
            "utility": _mean(r["utility"] for r in benign),
            "utility_under_attack": _mean(r["utility"] for r in attacks),
            "asr": _mean(r["security"] for r in attacks),
            "asr_executed_only": _mean(r.get("security_executed") for r in attacks),
            "tool_calls": sum(len(r.get("trace") or []) for r in scored),
            "blocked_calls": sum(e["blocked"] for r in scored for e in r.get("trace") or []),
            "invalid": [{"pair": f"{r['suite']}/{r['user_task']}/{r['injection_task'] or 'none'}",
                         "reason": r.get("invalid_reason")} for r in invalid],
        }
    return {"score_keys": list(SCORE_KEYS), "runs": len(records), "conditions": conditions}


def write_summary(out: str | Path) -> dict:
    summary = summarize(load_records(out))
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / SUMMARY_FILE).write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return summary


def set_agent_model(model: str | None) -> tuple[str, str]:
    """provider:model for every path: the native pipeline builds its agent from AGENTGATE_AGENT_* (make_llm)."""
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    agent_model = os.environ.get("AGENTGATE_AGENT_MODEL") or ""
    if model:
        provider, _, agent_model = model.partition(":")
        os.environ["AGENTGATE_AGENT_PROVIDER"], os.environ["AGENTGATE_AGENT_MODEL"] = provider, agent_model
    return provider, agent_model


def run_manifest(out: str | Path, args: dict, jobs: int, status: dict, elapsed_s: float) -> None:
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"run_{int(time.time())}.json").write_text(json.dumps(
        {"args": args, "jobs": jobs, "status": status, "elapsed_s": round(elapsed_s, 1)}, indent=1, default=str))
