"""Live evaluation (RQ2): run AgentDojo with an agent under conditions C0-C4 and log everything.

Conditions
  C0  no defense (gate in audit mode: records what it *would* decide, never blocks)
  C1  gate, ESCALATE -> block
  C2  gate, ESCALATE -> allow
  C3  AgentDojo built-in defense (--c3-defense, default spotlighting_with_delimiting), gate in audit mode
  C4  gate, ESCALATE -> judge LLM

Agents
  --agent llm       real LLM agent (keys from .env; AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL)
  --agent scripted  deterministic ScriptedAgent (no key) - checks the integration, not model behaviour

Frameworks (--framework, default native = the AgentDojo pipeline above, unchanged)
  Any name registered in agentgate.harness.registry (smolagents, contract Wrappers from config/frameworks.yaml,
  ...). The model comes from AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL (or --model provider:model).
  With --agent scripted the framework is driven by the LLM-free fake endpoint (agentgate.harness.fake_llm).
  C3 is native only.

Usage
  python scripts/evaluation/live_evaluation.py --agent scripted --conditions C0 C1 C2 --out results/live/v1/scripted
  python scripts/evaluation/live_evaluation.py --agent llm --pilot --conditions C0 C1 --out results/live/v1/<model>
  python scripts/evaluation/live_evaluation.py --framework smolagents --model ollama:gpt-oss:120b --pilot \
      --conditions C0 C1 --out results/live/v2/smolagents_gpt-oss_120b
Finished (condition, suite, user, injection) runs are skipped, so a run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import re
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.agents.factory import UsageMeter, make_llm, spec_provider
from agentgate.agents.scripted_agent import ScriptedAgent
from agentgate.config import load_dotenv
from agentgate.evaluation.replay import REPLAY_PIPELINE_NAME, get_cached_attack
from agentgate.llm.escalation_judge import EscalationJudge, as_runtime_judge
from agentgate.pipeline import RecordingPipeline, build_pipeline, run_live_task
from agentgate.runtime import GatedRuntime
from agentgate.suites import load_suite, task_number

PILOT = {"suites": ["banking"], "user_tasks": ["user_task_0", "user_task_2", "user_task_3", "user_task_13"],
         "injection_tasks": ["injection_task_0", "injection_task_4", "injection_task_7"]}
# Condition -> (gate mode, escalate action).
GATE = {"C0": ("audit", "block"), "C1": ("enforce", "block"), "C2": ("enforce", "allow"),
        "C3": ("audit", "block"), "C4": ("enforce", "judge")}


def run_framework_one(job: dict, output_path: Path) -> str:
    """One pair through agentgate.harness (any registered framework); same record schema as run_one."""
    from agentgate.harness.fake_llm import FakeLLMServer, ScriptedPlan
    from agentgate.harness.models import ModelSpec
    from agentgate.harness.runner import RunRequest, execute, run_scenario
    from agentgate.harness.scenario import ScenarioConfig, Session

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


def run_one(job: dict) -> str:
    load_dotenv()
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
    mode, escalate_action = GATE[condition]
    gated = condition in ("C1", "C2", "C4")
    provider = spec_provider(job["spec"] if gated else "oracle", suite_name, user_task_id)
    judge = None
    runtime_kwargs = {"mode": mode, "escalate_action": escalate_action, "record_env_hash": True}
    if condition == "C4":
        judge = EscalationJudge()
        runtime_kwargs["judge"] = as_runtime_judge(judge, user_task.PROMPT)
    runtime = GatedRuntime(suite.tools, **runtime_kwargs)
    pipeline = build_pipeline(llm, provider, defense=job["c3_defense"] if condition == "C3" else None)
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
        "spec_source": job["spec"] if gated else "oracle(audit)", "spec_meta": runtime.spec_meta,
        "error": error, **result,
        "agent_tokens": {"input": meter.input_tokens, "output": meter.output_tokens, "calls": meter.calls,
                         "llm_ms": round(meter.ms, 1), "refusals": meter.refusals},
        "gate_ms": round(sum(e.decide_ms for e in runtime.trace), 3),
        "judge": judge.stats if judge else None,
        "trace": runtime.trace_records(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, ensure_ascii=False, default=str))
    return "error" if error else "ok"


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--conditions", nargs="+", default=["C0", "C1"])
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--user-tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", nargs="*", default=None)
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--attack-model-name", default=None, help="name used for {model} in the attack text")
    parser.add_argument("--spec", choices=["llm", "oracle"], default="llm")
    parser.add_argument("--c3-defense", default="spotlighting_with_delimiting")
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
    parser.add_argument("--model", default=None, help="provider:model for --framework (default from .env)")
    parser.add_argument("--max-steps", type=int, default=None, help="--framework step limit (default 15)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.pilot:
        args.suites, args.user_tasks, args.injection_tasks = (PILOT["suites"], PILOT["user_tasks"],
                                                              PILOT["injection_tasks"])
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    agent_model = os.environ.get("AGENTGATE_AGENT_MODEL") or ""
    if args.model:
        provider, _, agent_model = args.model.partition(":")
    if args.framework != "native" and "C3" in args.conditions:
        parser.error("C3 (AgentDojo pipeline defense) is native only")
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
                             "c3_defense": args.c3_defense, "on_block": args.on_block, "out": args.out,
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
                        "c3_defense": args.c3_defense, "on_block": args.on_block, "out": args.out,
                        **framework_fields}
                if not args.no_benign:
                    jobs.append({**base, "injection_task": None})
                for injection_task_id in injection_task_ids:
                    jobs.append({**base, "injection_task": injection_task_id})
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
