"""Stage 6/7: run AgentDojo with an agent under conditions C0-C4 and log everything.

Conditions
  C0  no defense (gate in audit mode: records what it *would* decide, never blocks)
  C1  gate, ESCALATE -> block
  C2  gate, ESCALATE -> allow
  C3  AgentDojo built-in defense (--c3-defense, default spotlighting_with_delimiting), gate in audit mode
  C4  gate, ESCALATE -> judge LLM (Stage 7)

Agents
  --agent llm       real LLM agent (keys from .env; AGENTGATE_AGENT_PROVIDER / AGENTGATE_AGENT_MODEL)
  --agent scripted  deterministic ScriptedAgent (no key) - checks the integration, not model behaviour

Examples
  python scripts/live_eval.py --agent scripted --conditions C0 C1 C2 --out results/live/v1/scripted
  python scripts/live_eval.py --agent llm --pilot --conditions C0 C1 --out results/live/v1/<model>
Finished (condition, suite, user, injection) runs are skipped, so a run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import re
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.env import load_dotenv
from agentgate.llm.client import DEFAULT_ANTHROPIC_MODEL
from agentgate.pipeline import RecordingPipeline, ScriptedAgent, build_pipeline, run_live_task
from agentgate.replay import REPLAY_PIPELINE_NAME, _attack
from agentgate.runtime import GatedRuntime
from agentgate.spec import TaskSpec, load_spec
from agentgate.suites import load_suite, tool_infos

ROOT = Path(__file__).resolve().parents[1]
PILOT = {"suites": ["banking"], "user_tasks": ["user_task_0", "user_task_2", "user_task_3", "user_task_13"],
         "injection_tasks": ["injection_task_0", "injection_task_4", "injection_task_7"]}
GATE = {"C0": ("audit", "block"), "C1": ("enforce", "block"), "C2": ("enforce", "allow"),
        "C3": ("audit", "block"), "C4": ("enforce", "judge")}


class UsageMeter:
    """Agent LLM usage. ClaudeAgentLLM counts its own usage; OpenAI clients are wrapped."""

    def __init__(self):
        self.input_tokens = self.output_tokens = self.calls = 0
        self.ms = 0.0
        self.llm = None
        self.refusals = 0

    def collect(self):
        if self.llm is not None and hasattr(self.llm, "usage"):
            u = self.llm.usage
            self.input_tokens, self.output_tokens, self.calls, self.ms = (u["input_tokens"], u["output_tokens"],
                                                                         u["calls"], u["ms"])
            self.refusals = u.get("refusals", 0)

    def wrap_openai(self, client):
        orig = client.chat.completions.create

        def create(*a, **k):
            t0 = time.perf_counter()
            r = orig(*a, **k)
            self.ms += (time.perf_counter() - t0) * 1000
            self.calls += 1
            self.input_tokens += r.usage.prompt_tokens or 0
            self.output_tokens += r.usage.completion_tokens or 0
            return r

        client.chat.completions.create = create
        return client


def make_llm(meter: UsageMeter):
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    model = os.environ.get("AGENTGATE_AGENT_MODEL") or (DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else "")
    if not model:
        raise RuntimeError("AGENTGATE_AGENT_MODEL is not set")
    if provider == "anthropic":
        import anthropic

        from agentgate.agent_llm import ClaudeAgentLLM
        llm = ClaudeAgentLLM(anthropic.Anthropic(), model)
        meter.llm = llm
    elif provider == "ollama":
        from agentgate.ollama_llm import OllamaAgentLLM
        if not model:
            raise RuntimeError("AGENTGATE_AGENT_MODEL is not set (an Ollama model name, e.g. from GET /api/tags)")
        llm = OllamaAgentLLM(model)
        meter.llm = llm
    elif provider == "openai":
        import openai
        from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
        llm = OpenAILLM(meter.wrap_openai(openai.OpenAI()), model, temperature=0.0)
        llm.name = model
    else:
        raise ValueError(provider)
    return llm


def spec_provider(kind: str, suite_name: str, user_task_id: str):
    def oracle(prompt):
        return load_spec("oracle", suite_name, user_task_id), {"source": "oracle", "cached": True, "latency_ms": 0.0}

    if kind == "oracle":
        return oracle

    def llm(prompt):
        from agentgate.llm.extractor import TaskSpecExtractor, ToolInfo
        tools = [ToolInfo(**t) for t in tool_infos(load_suite(suite_name))]
        r = TaskSpecExtractor().extract(prompt, tools)
        meta = {"source": f"llm:{r.model}", "cached": r.cached, "latency_ms": 0.0 if r.cached else r.latency_ms,
                "input_tokens": 0 if r.cached else r.input_tokens, "output_tokens": 0 if r.cached else r.output_tokens,
                "error": r.error}
        # extraction failure -> fail closed with an empty spec (nothing requested)
        return (r.spec or TaskSpec(notes="extraction failed")), meta

    return llm


def run_one(job: dict) -> str:
    load_dotenv()
    cond, s, ut_id, it_id = job["cond"], job["suite"], job["user_task"], job["injection_task"]
    out = Path(job["out"]) / cond / s / ut_id / f"{it_id or 'none'}.json"
    if out.exists():
        return "skip"
    suite = load_suite(s)
    ut = suite.get_user_task_by_id(ut_id)
    it = suite.get_injection_task_by_id(it_id) if it_id else None
    meter = UsageMeter()
    if job["agent"] == "scripted":
        llm = ScriptedAgent(s, ut, it, on_block=job["on_block"])
        attack_model = "GPT-4"
    else:
        llm = make_llm(meter)
        attack_model = job["attack_model_name"]
    mode, esc = GATE[cond]
    gated = cond in ("C1", "C2", "C4")
    provider = spec_provider(job["spec"] if gated else "oracle", s, ut_id)
    judge_obj = None
    rt_kw = {"mode": mode, "escalate_action": esc, "record_env_hash": True}
    if cond == "C4":
        from agentgate.llm.judge import EscalationJudge, as_runtime_judge
        judge_obj = EscalationJudge()
        rt_kw["judge"] = as_runtime_judge(judge_obj, ut.PROMPT)
    runtime = GatedRuntime(suite.tools, **rt_kw)
    pipe = build_pipeline(llm, provider, defense=job["c3_defense"] if cond == "C3" else None)
    rec = RecordingPipeline(pipe)
    injections = {}
    if it is not None:
        attack = _attack(s, job["attack"], REPLAY_PIPELINE_NAME)
        attack.model_name = attack_model
        injections = attack.attack(ut, it)
    try:
        res = run_live_task(suite, rec, ut, it, injections, runtime)
        err = None
    except Exception as e:  # keep going; record the failure
        res, err = {}, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
    meter.collect()
    record = {
        "condition": cond, "suite": s, "user_task": ut_id, "injection_task": it_id, "agent": job["agent"],
        "agent_model": getattr(llm, "name", None), "attack": job["attack"] if it else None,
        "spec_source": job["spec"] if gated else "oracle(audit)", "spec_meta": runtime.spec_meta,
        "error": err, **res,
        "agent_tokens": {"input": meter.input_tokens, "output": meter.output_tokens, "calls": meter.calls,
                         "llm_ms": round(meter.ms, 1), "refusals": meter.refusals},
        "gate_ms": round(sum(e.decide_ms for e in runtime.trace), 3),
        "judge": judge_obj.stats if judge_obj else None,
        "trace": runtime.trace_records(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, default=str))
    return "error" if err else "ok"


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    ap.add_argument("--conditions", nargs="+", default=["C0", "C1"])
    ap.add_argument("--suites", nargs="*", default=list(SUITES))
    ap.add_argument("--user-tasks", nargs="*", default=None)
    ap.add_argument("--injection-tasks", nargs="*", default=None)
    ap.add_argument("--attack", default="important_instructions")
    ap.add_argument("--attack-model-name", default=None, help="name used for {model} in the attack text")
    ap.add_argument("--spec", choices=["llm", "oracle"], default="llm")
    ap.add_argument("--c3-defense", default="spotlighting_with_delimiting")
    ap.add_argument("--on-block", choices=["continue", "stop"], default="continue", help="scripted agent only")
    ap.add_argument("--no-benign", action="store_true", help="skip the no-attack (utility) runs")
    ap.add_argument("--pilot", action="store_true", help="banking, 4 user x 3 injection tasks")
    ap.add_argument("--sample", type=int, default=0,
                    help="per suite, N user tasks drawn with --seed (all injection tasks for each)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pairs", default=None,
                    help="explicit pairs 'suite:user_task:injection_task,...' or @file (one per line); "
                         "injection_task 'none' = benign run (overrides suites/tasks)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.pilot:
        a.suites, a.user_tasks, a.injection_tasks = PILOT["suites"], PILOT["user_tasks"], PILOT["injection_tasks"]
    provider = os.environ.get("AGENTGATE_AGENT_PROVIDER") or "anthropic"
    agent_model = os.environ.get("AGENTGATE_AGENT_MODEL") or ""
    attack_model = a.attack_model_name or {"anthropic": "Claude", "openai": "GPT-4"}.get(
        provider, re.split(r"[-:_]", agent_model)[0].upper() or "AI assistant")
    jobs = []
    if a.pairs:
        for cond in a.conditions:
            items = (Path(a.pairs[1:]).read_text().split() if a.pairs.startswith("@") else a.pairs.split(","))
            for item in items:
                s, ut, it = item.strip().split(":")
                it = None if it == "none" else it
                jobs.append({"cond": cond, "suite": s, "user_task": ut, "injection_task": it, "agent": a.agent,
                             "spec": a.spec, "attack": a.attack, "attack_model_name": attack_model,
                             "c3_defense": a.c3_defense, "on_block": a.on_block, "out": a.out})
        a.conditions = []   # skip the suite loop below
    for cond in a.conditions:
        for s in a.suites:
            suite = load_suite(s)
            uts = a.user_tasks or sorted(suite.user_tasks, key=lambda x: int(x.split("_")[-1]))
            if a.sample:
                uts = sorted(random.Random(f"{a.seed}:{s}").sample(uts, a.sample), key=lambda x: int(x.split("_")[-1]))
            its = a.injection_tasks or sorted(suite.injection_tasks, key=lambda x: int(x.split("_")[-1]))
            for ut in uts:
                base = {"cond": cond, "suite": s, "user_task": ut, "agent": a.agent, "spec": a.spec,
                        "attack": a.attack, "attack_model_name": attack_model, "c3_defense": a.c3_defense,
                        "on_block": a.on_block, "out": a.out}
                if not a.no_benign:
                    jobs.append({**base, "injection_task": None})
                for it in its:
                    jobs.append({**base, "injection_task": it})
    t0 = time.time()
    if a.agent == "scripted":
        with mp.Pool(a.workers) as pool:
            status = list(pool.imap_unordered(run_one, jobs, chunksize=8))
    else:
        with ThreadPoolExecutor(a.workers) as ex:
            status = list(ex.map(run_one, jobs))
    from collections import Counter
    print(f"{len(jobs)} jobs in {time.time() - t0:.0f}s: {dict(Counter(status))}")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"run_{int(time.time())}.json").write_text(json.dumps(
        {"args": vars(a), "jobs": len(jobs), "status": dict(Counter(status)), "elapsed_s": round(time.time() - t0, 1)},
        indent=1))


if __name__ == "__main__":
    main()
