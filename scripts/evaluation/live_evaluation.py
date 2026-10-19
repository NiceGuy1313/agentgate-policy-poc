"""live 평가(RQ2): 한 실행 안에서 여러 조건으로 에이전트를 AgentDojo에 돌리고 전 과정을 기록한다.

조건(`agentgate.experiment.conditions`): no_gate 기준선, gate_llm_judge, gate_static_rules. 조건을 비교하려면 같은
실행에 기준선 no_gate를 함께 넣는다(같은 짝·공격·모델·시점). 다른 실행의 no_gate와 섞어 비교하지 않는다.

규모
  기본(--pairs, --pilot, --sample 없음): AgentDojo v1.2.2 전체 = 사용자 작업 97개(공격 없음) + 공격 쌍 949개
  --pairs @data/agentdojo/pairs/strat3_seed0.txt: 사용자 작업마다 공격 3개(층화 표본) = 97 + 291
  --pilot: banking 4 × 3

에이전트
  --agent llm       실제 LLM 에이전트 (키는 .env; AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL)
  --agent scripted  결정론적 ScriptedAgent (키 불필요) - 모델 행동이 아니라 통합을 확인한다

프레임워크 (--framework, 기본값 native = AgentDojo pipeline)
  agentgate.harness.registry에 등록된 이름.

사용법
  python scripts/evaluation/live_evaluation.py --agent scripted --conditions no_gate gate_static_rules --out results/live/scripted
  python scripts/evaluation/live_evaluation.py --agent llm --conditions no_gate gate_llm_judge gate_static_rules \
      --attack important_instructions --out results/live/<run>
완료된 (condition, suite, user, injection) run은 건너뛰므로 중단된 실행을 이어서 할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import re
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.agents.factory import UsageMeter, make_llm, spec_provider
from agentgate.agents.scripted_agent import ScriptedAgent
from agentgate.config import load_dotenv
from agentgate.experiment.conditions import CONDITIONS, get_condition, make_runtime
from agentgate.scenarios.pipeline import RecordingPipeline, build_pipeline, run_live_task
from agentgate.scenarios.replay import REPLAY_PIPELINE_NAME, get_cached_attack
from agentgate.scenarios.suites import load_suite, task_number

PILOT = {"suites": ["banking"], "user_tasks": ["user_task_0", "user_task_2", "user_task_3", "user_task_13"],
         "injection_tasks": ["injection_task_0", "injection_task_4", "injection_task_7"]}


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
    return "error" if record.get("error") else "ok"


_QUOTA_EXHAUSTED = threading.Event()


def _is_quota_error(error: str | None) -> bool:
    """Provider usage limit (e.g. Ollama Cloud 'You used all included ... usage', HTTP 429): retrying cannot help."""
    return bool(error) and "429" in error and "usage" in error.lower()


def run_one(job: dict) -> str:
    load_dotenv()
    if _QUOTA_EXHAUSTED.is_set():
        return "quota"
    condition, suite_name = job["cond"], job["suite"]
    user_task_id, injection_task_id = job["user_task"], job["injection_task"]
    output_path = Path(job["out"]) / condition / suite_name / user_task_id / f"{injection_task_id or 'none'}.json"
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
    if _is_quota_error(error):
        # no record: the run did not happen, so a later invocation retries it instead of skipping it
        _QUOTA_EXHAUSTED.set()
        print(f"usage limit reached, stopping: {error.splitlines()[0][:200]}", flush=True)
        return "quota"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, default=str))
    return "error" if error else "ok"


def _extract_specs_once(jobs: list[dict]) -> None:
    """One extraction per (suite, user task) before any run, so concurrent runs of the same task never extract it
    separately (they all read the same cached spec)."""
    from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
    from agentgate.scenarios.suites import describe_tools
    keys = sorted({(j["suite"], j["user_task"]) for j in jobs if j.get("framework", "native") == "native"})
    items = []
    for suite_name, user_task_id in keys:
        suite = load_suite(suite_name)
        items.append((suite.get_user_task_by_id(user_task_id).PROMPT, [ToolInfo(**tool) for tool in describe_tools(suite)]))
    # one Message Batches request for every cache miss (anthropic): 50% price, prompt-cached tool list
    results = TaskSpecExtractor().extract_many(items)
    print(f"TaskSpec: {len(keys)} extracted ({sum(not r.cached for r in results)} live, "
          f"{sum(r.spec is None for r in results)} failed) -> {os.environ['AGENTGATE_TASKSPEC_CACHE_DIR']}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--conditions", nargs="+", default=["no_gate", "gate_static_rules"], choices=list(CONDITIONS))
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--user-tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", nargs="*", default=None)
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--attack-model-name", default=None, help="name used for {model} in the attack text")
    parser.add_argument("--spec", choices=["llm", "oracle"], default="llm")
    parser.add_argument("--on-block", choices=["continue", "stop"], default="continue", help="scripted agent only")
    parser.add_argument("--no-benign", action="store_true", help="skip the no-attack (utility) runs")
    parser.add_argument("--pilot", action="store_true", help="banking, 4 user x 3 injection tasks")
    parser.add_argument("--sample", type=int, default=0,
                        help="per suite, N user tasks drawn with --seed (all injection tasks for each)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pairs", default=None,
                        help="explicit pairs 'suite:user_task:injection_task,...' or @file (one per line); "
                             "injection_task 'none' = benign run (overrides suites/tasks)")
    parser.add_argument("--framework", default="native",
                        help="agent framework from agentgate.harness.registry (default native = AgentDojo pipeline)")
    parser.add_argument("--framework-options", default="{}", help="framework options as JSON")
    parser.add_argument("--model", default=None, help="agent provider:model, e.g. ollama:gpt-oss:120b (default from .env)")
    parser.add_argument("--max-steps", type=int, default=None, help="--framework step limit (default 15)")
    parser.add_argument("--spec-cache-dir", default=None,
                        help="TaskSpec extraction cache dir; a new directory makes the extractor call the LLM live")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    # live TaskSpec: extracted fresh for this run (cache inside the run directory) unless --spec-cache-dir reuses one
    os.environ["AGENTGATE_TASKSPEC_CACHE_DIR"] = str(Path(args.spec_cache_dir or Path(args.out) / "taskspec_cache")
                                                     .resolve())
    if args.pilot:
        args.suites, args.user_tasks, args.injection_tasks = (PILOT["suites"], PILOT["user_tasks"],
                                                              PILOT["injection_tasks"])
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    agent_model = os.environ.get("AGENTGATE_AGENT_MODEL") or ""
    if args.model:
        provider, _, agent_model = args.model.partition(":")
        # the native pipeline builds its agent from these variables (make_llm); worker threads share them
        os.environ["AGENTGATE_AGENT_PROVIDER"], os.environ["AGENTGATE_AGENT_MODEL"] = provider, agent_model
    conditions = list(args.conditions)
    for condition in conditions:
        get_condition(condition)
    attack_model = args.attack_model_name or {"anthropic": "Claude", "openai": "GPT-4"}.get(
        provider, re.split(r"[-:_]", agent_model)[0].upper() or "AI assistant")
    framework_fields = {"framework": args.framework, "framework_options": args.framework_options,
                        "model": f"{provider}:{agent_model}", "max_steps": args.max_steps}
    jobs = []
    if args.pairs:
        for condition in args.conditions:
            items = (Path(args.pairs[1:]).read_text().split() if args.pairs.startswith("@")
                     else args.pairs.split(","))
            for item in items:
                suite_name, user_task_id, injection_task_id = item.strip().split(":")
                injection_task_id = None if injection_task_id == "none" else injection_task_id
                jobs.append({"cond": condition, "suite": suite_name, "user_task": user_task_id,
                             "injection_task": injection_task_id, "agent": args.agent,
                             "spec": args.spec, "attack": args.attack, "attack_model_name": attack_model,
                             "on_block": args.on_block, "out": args.out,
                             **framework_fields})
        args.conditions = []   # skip the suite loop below
    for condition in args.conditions:
        for suite_name in args.suites:
            suite = load_suite(suite_name)
            user_task_ids = args.user_tasks or sorted(suite.user_tasks, key=task_number)
            if args.sample:
                user_task_ids = sorted(random.Random(f"{args.seed}:{suite_name}").sample(user_task_ids, args.sample),
                                       key=task_number)
            injection_task_ids = args.injection_tasks or sorted(suite.injection_tasks, key=task_number)
            for user_task_id in user_task_ids:
                base = {"cond": condition, "suite": suite_name, "user_task": user_task_id, "agent": args.agent,
                        "spec": args.spec, "attack": args.attack, "attack_model_name": attack_model,
                        "on_block": args.on_block, "out": args.out,
                        **framework_fields}
                if not args.no_benign:
                    jobs.append({**base, "injection_task": None})
                for injection_task_id in injection_task_ids:
                    jobs.append({**base, "injection_task": injection_task_id})
    if args.spec == "llm":
        _extract_specs_once(jobs)
    # all runs of one user task (every condition, benign and attack) back to back: the judge's prompt-cache prefix
    # (system + TaskSpec) stays warm within its 5-minute TTL. Runs are independent, so the order does not change results.
    jobs.sort(key=lambda j: (j["suite"], task_number(j["user_task"]), j["injection_task"] or "", j["cond"]))
    started = time.time()
    if args.agent == "scripted" and args.framework == "native":
        with mp.Pool(args.workers) as pool:
            status = list(pool.imap_unordered(run_one, jobs, chunksize=8))
    else:
        with ThreadPoolExecutor(args.workers) as executor:
            status = list(executor.map(run_one, jobs))
    print(f"{len(jobs)} jobs in {time.time() - started:.0f}s: {dict(Counter(status))}")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / f"run_{int(time.time())}.json").write_text(json.dumps(
        {"args": vars(args), "jobs": len(jobs), "status": dict(Counter(status)),
         "elapsed_s": round(time.time() - started, 1)},
        indent=1))


if __name__ == "__main__":
    main()
