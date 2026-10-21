"""여러 Agent 프레임워크의 실행 결과를 집계해 results/summary/frameworks.json과 results/figures/frameworks.png를 만든다.

입력: 프레임워크마다 같은 표본(--pairs)으로 돌린 실행 디렉터리(results/runs/<name>, 기록의 framework 필드로 구분).
출력: 조건별 ASR·Utility·Utility under Attack(Wilson 신뢰구간)·차단 호출 수, 같은 짝 비교(exact McNemar).
      보고서 results/RESULTS.md는 scripts/analysis/write_report.py가 이 JSON으로 만든다. Gate 우회로 무효인 run은 제외.
사용법: python scripts/analysis/summarize_frameworks.py --runs results/runs/strat_smolagents results/runs/strat_mini-swe-agent
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402
from agentgate.experiment.live_run_analysis import live_run_files  # noqa: E402
from agentgate.experiment.metrics import mcnemar_exact, wilson  # noqa: E402

CONDITIONS = ["no_gate", "gate_llm_judge", "gate_static_rules"]
NAMES = {"no_gate": "No Gate", "gate_llm_judge": "LLM Judge (Policy)", "gate_static_rules": "Static Checker (Policy)"}
LABEL = {"no_gate": "No Gate", "gate_llm_judge": "LLM Judge (Policy)", "gate_static_rules": "Static Checker (Policy)"}
COLOR = {"no_gate": "#9aa0a6", "gate_static_rules": "#1a73e8", "gate_llm_judge": "#e8710a"}
METRICS = [("asr", "ASR"), ("utility", "Utility"), ("uua", "Utility under Attack")]


def load(run_dirs: list[Path]) -> tuple[list[dict], Counter]:
    runs, skipped = [], Counter()
    for run_dir in run_dirs:
        for path in live_run_files(run_dir):
            run = json.loads(path.read_text())
            if "condition" not in run:
                continue
            if run.get("error"):
                skipped["error"] += 1
                continue
            if run.get("valid") is False:
                skipped["invalid"] += 1
                continue
            run.setdefault("framework", run_dir.name.removeprefix("strat_"))
            runs.append(run)
    return runs, skipped


def outcome(run: dict, metric: str) -> bool | None:
    if metric == "utility":
        return bool(run["utility"]) if run["injection_task"] is None else None
    if run["injection_task"] is None:
        return None
    return bool(run["security"]) if metric == "asr" else bool(run["utility"])


def rate(values: list[bool]) -> dict:
    k, n = sum(values), len(values)
    lo, hi = wilson(k, n) or (0.0, 0.0)
    return {"k": k, "n": n, "rate": k / n if n else 0.0, "ci95": [lo, hi]}


def paired(runs: list[dict], framework: str, a: str, b: str, metric: str) -> dict:
    def key(r):
        return r["suite"], r["user_task"], r["injection_task"]
    va = {key(r): outcome(r, metric) for r in runs if r["framework"] == framework and r["condition"] == a}
    vb = {key(r): outcome(r, metric) for r in runs if r["framework"] == framework and r["condition"] == b}
    keys = [k for k in va.keys() & vb.keys() if va[k] is not None and vb[k] is not None]
    a_only = sum(va[k] and not vb[k] for k in keys)
    b_only = sum(vb[k] and not va[k] for k in keys)
    return {"diff": (a_only - b_only) / len(keys) if keys else None, "p": mcnemar_exact(b_only, a_only),
            "n": len(keys)}


def diff_text(d: dict) -> str:
    if d["diff"] is None:
        return "-"
    return f"{100 * d['diff']:+.1f}%p (p={d['p']:.3g}{', 유의' if d['p'] < 0.05 else ''})"


def figure(table: dict, frameworks: list[str], out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    width = 0.8 / len(CONDITIONS)
    for ax, (metric, title) in zip(axes, [("asr", "ASR (lower is better)"), ("utility", "Utility (benign)"),
                                          ("uua", "Utility under attack")]):
        for i, c in enumerate(CONDITIONS):
            vals = [100 * table[f][c][metric]["rate"] for f in frameworks]
            lo = [100 * (table[f][c][metric]["rate"] - table[f][c][metric]["ci95"][0]) for f in frameworks]
            hi = [100 * (table[f][c][metric]["ci95"][1] - table[f][c][metric]["rate"]) for f in frameworks]
            xs = [j + (i - (len(CONDITIONS) - 1) / 2) * width for j in range(len(frameworks))]
            ax.bar(xs, vals, width, yerr=[lo, hi], capsize=2, color=COLOR[c], label=LABEL[c])
        ax.set_xticks(range(len(frameworks)), frameworks, fontsize=8)
        ax.set_ylim(0, 105)
        ax.set_title(title)
        ax.set_ylabel("%")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Agent frameworks × verification method (stratified sample, 95% Wilson CI)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--pairs", default="data/agentdojo/pairs/strat3_seed0.txt", help="sample name for the text")
    args = parser.parse_args()
    runs, skipped = load([Path(p).resolve() for p in args.runs])
    frameworks = [f for f in ["native", "smolagents", "mini-swe-agent"]
                  if any(r["framework"] == f for r in runs)]
    frameworks += sorted({r["framework"] for r in runs} - set(frameworks))
    table: dict = defaultdict(dict)
    for f in frameworks:
        for c in CONDITIONS:
            cell = [r for r in runs if r["framework"] == f and r["condition"] == c]
            table[f][c] = {m: rate([v for r in cell if (v := outcome(r, m)) is not None]) for m, _ in METRICS}
            table[f][c]["blocked"] = sum(e.get("blocked", False) for r in cell for e in r["trace"])
    fig_dir = PROJECT_ROOT / "results" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    figure(table, frameworks, fig_dir / "frameworks.png")
    summary_dir = PROJECT_ROOT / "results" / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    comparisons = [("gate_llm_judge", "no_gate"), ("gate_static_rules", "no_gate"), ("gate_llm_judge", "gate_static_rules")]
    paired_table = {f: {f"{a} - {b}": {m: paired(runs, f, a, b, m) for m, _ in METRICS} for a, b in comparisons}
                    for f in frameworks}
    (summary_dir / "frameworks.json").write_text(json.dumps(
        {"sample": args.pairs, "skipped": dict(skipped), "table": table, "paired": paired_table},
        indent=1, ensure_ascii=False))

    print(f"wrote {fig_dir / 'frameworks.png'}, {summary_dir / 'frameworks.json'} (report: write_report.py)")


if __name__ == "__main__":
    main()
