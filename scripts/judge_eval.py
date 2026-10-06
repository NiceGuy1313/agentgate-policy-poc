"""Stage 7 task 2: the judge alone on every ESCALATE of the Stage 5 replay.

Ground truth label per escalated call: `attack` if it is an effect call of the injection task in an
attack replay, `normal` if it comes from a benign (no-injection) replay. Writes
results/judge/v1/<model>/judge_eval.json with the A/B/C confusion matrix and HUMAN rate.

  python scripts/judge_eval.py [--suites banking] [--max-pairs 0]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.llm.judge import EscalationJudge
from agentgate.replay import run_attack, run_benign
from agentgate.spec import load_spec
from agentgate.suites import load_suite

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="*", default=list(SUITES))
    ap.add_argument("--max-pairs", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    try:
        judge = EscalationJudge()
    except RuntimeError as e:
        raise SystemExit(f"not run: {e}")
    replay = ROOT / "results/replay/v1/oracle"
    records = []

    def collect(r, label_fn):
        for e in r["trace"]:
            if e.get("judge"):
                records.append({"suite": r["suite"], "user_task": r["user_task"],
                                "injection_task": r.get("injection_task"), "tool": e["tool"],
                                "label": label_fn(e), **{k: e["judge"][k] for k in ("answer", "final", "human")},
                                "facts": e["judge"]["facts"]})

    # only pairs whose audit replay produced an ESCALATE on a relevant call
    esc_attack = set()
    for line in (replay / "attack_audit.jsonl").open():
        r = json.loads(line)
        if any(e["verdict"] == "ESCALATE" and e.get("role") == "effect" for e in r["trace"]):
            esc_attack.add((r["suite"], r["user_task"], r["injection_task"]))
    esc_benign = set()
    for line in (replay / "benign_audit.jsonl").open():
        r = json.loads(line)
        if any(e["verdict"] == "ESCALATE" for e in r["trace"]):
            esc_benign.add((r["suite"], r["user_task"]))
    pairs = sorted(p for p in esc_attack if p[0] in a.suites)
    if a.max_pairs:
        pairs = pairs[: a.max_pairs]
    def benign_job(s, ut):
        spec = load_spec("oracle", s, ut)
        prompt = load_suite(s).get_user_task_by_id(ut).PROMPT
        r = run_benign(s, ut, spec, "audit", runtime_kw={"escalate_action": "judge",
                                                         "judge": lambda c, res, ctx: judge(c, res, ctx, prompt)})
        return {"suite": s, "user_task": ut, "trace": _raw(r)}, (lambda e: "normal")

    def attack_job(s, ut, it):
        spec = load_spec("oracle", s, ut)
        prompt = load_suite(s).get_user_task_by_id(ut).PROMPT
        r = run_attack(s, ut, it, spec, "audit", runtime_kw={"escalate_action": "judge",
                                                             "judge": lambda c, res, ctx: judge(c, res, ctx, prompt)})
        # only the injection task's calls are labelled: user-phase calls in an injected env are ambiguous
        tr = [e for e in _raw(r) if e["phase"] == "injection"]
        return {"suite": s, "user_task": ut, "injection_task": it, "trace": tr}, (lambda e: "attack")

    jobs = [(benign_job, (s, ut)) for s, ut in sorted(esc_benign) if s in a.suites]
    jobs += [(attack_job, p) for p in pairs]
    with ThreadPoolExecutor(a.workers) as ex:
        for run, label_fn in ex.map(lambda j: j[0](*j[1]), jobs):
            collect(run, label_fn)
    cm = Counter((x["label"], x["answer"]) for x in records)
    finals = Counter((x["label"], x["final"]) for x in records)
    out = {"model": judge.model, "n": len(records),
           "confusion_answer": {f"{k[0]}->{k[1]}": v for k, v in sorted(cm.items())},
           "confusion_final": {f"{k[0]}->{k[1]}": v for k, v in sorted(finals.items())},
           "human_rate": sum(x["human"] for x in records) / len(records) if records else None,
           "stats": judge.stats, "records": records}
    d = ROOT / "results/judge/v1" / judge.model.replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / "judge_eval.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "records"}, indent=1))


def _raw(r):
    """run_* return trace_records(); judge output is inside the decision dict."""
    return r["trace"]


if __name__ == "__main__":
    main()
