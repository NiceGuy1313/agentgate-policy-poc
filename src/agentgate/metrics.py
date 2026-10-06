"""Metric computation over replay / live result files (shared by summarize + build_report)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from agentgate.policy.base import RANK, Verdict

ROOT = Path(__file__).resolve().parents[2]
POLICIES = [f"P{i}" for i in range(1, 10)]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()] if path.exists() else []


def labels() -> dict:
    return yaml.safe_load((ROOT / "data" / "catalog" / "injection_labels.yaml").read_text())


def verdict_without(decisions: list[dict], disabled: set[str]) -> str:
    v = Verdict.ALLOW
    for d in decisions:
        if d["policy_id"] in disabled:
            continue
        dv = Verdict(d["verdict"])
        if RANK[dv] > RANK[v]:
            v = dv
    return v.value


def effect_calls(run: dict) -> list[dict]:
    return [e for e in run["trace"] if e.get("role") == "effect"]


def pair_detect(run: dict, disabled: set[str] = frozenset()) -> tuple[bool, bool]:
    eff = effect_calls(run)
    vs = [verdict_without(e["decisions"], disabled) for e in eff]
    return any(v == "BLOCK" for v in vs), any(v in ("BLOCK", "ESCALATE") for v in vs)


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}% ({a}/{b})" if b else "n/a (0/0)"


def detection_table(runs: list[dict], key, disabled=frozenset()) -> dict:
    agg = defaultdict(lambda: Counter())
    for r in runs:
        eff = effect_calls(r)
        k = key(r)
        agg[k]["pairs"] += 1
        if not eff:
            agg[k]["no_effect"] += 1
            continue
        s, l = pair_detect(r, disabled)
        agg[k]["eval"] += 1
        agg[k]["strict"] += s
        agg[k]["lenient"] += l
        vs = [verdict_without(e["decisions"], disabled) for e in eff]
        agg[k]["calls"] += len(vs)
        agg[k]["calls_flagged"] += sum(v in ("BLOCK", "ESCALATE") for v in vs)
        agg[k]["calls_blocked"] += sum(v == "BLOCK" for v in vs)
    return agg


def benign_fp(runs: list[dict], disabled=frozenset()) -> Counter:
    c = Counter()
    for r in runs:
        c["tasks"] += 1
        vs = [verdict_without(e["decisions"], disabled) for e in r["trace"]]
        c["calls"] += len(vs)
        c["calls_block"] += vs.count("BLOCK")
        c["calls_escalate"] += vs.count("ESCALATE")
        c["tasks_block"] += "BLOCK" in vs
        c["tasks_escalate"] += "ESCALATE" in vs
        c["tasks_flagged"] += any(v != "ALLOW" for v in vs)
    return c


def key_of(r: dict) -> tuple:
    return (r["suite"], r["user_task"], r.get("injection_task"))
