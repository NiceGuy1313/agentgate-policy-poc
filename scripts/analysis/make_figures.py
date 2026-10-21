"""실험 결과 그림을 results/figures/에 만든다. 수치는 모두 실행 기록(run json)과 significance.json에서 계산한다.

그림: 조건별 핵심 지표(신뢰구간), suite별 ASR·Utility, 보안-유용성 trade-off, 판정 규칙별 BLOCK 수,
재생 대조(같은 입력에서 LLM Judge 대 정적 규칙)의 호출 단위 차단률(--contrast 파일이 있을 때).
선행: summarize_live.py, live_significance.py를 같은 --run에 실행
사용법: python scripts/analysis/make_figures.py --run results/runs/<run> [--contrast results/replay_contrast.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402
from agentgate.experiment.live_run_analysis import live_run_files  # noqa: E402
from agentgate.experiment.metrics import wilson  # noqa: E402

FIG_DIR = PROJECT_ROOT / "results" / "figures"
ORDER = ["no_gate", "gate_static_rules", "gate_llm_judge"]
LABEL = {"no_gate": "No Gate", "gate_static_rules": "Static Checker (Policy)", "gate_llm_judge": "LLM Judge (Policy)"}
COLOR = {"no_gate": "#9aa0a6", "gate_static_rules": "#1a73e8", "gate_llm_judge": "#e8710a"}
METRICS = [("asr", "ASR (lower is better)"), ("utility", "Utility (benign)"),
           ("utility_under_attack", "Utility under attack")]
RULES = [f"R{i}" for i in range(1, 11)]
RULE_ID = re.compile(r"\bR(?:10|[1-9])\b")


def judge_rule_ids(judge: dict) -> list[str]:
    """Rule ids of a judgement: the static checker's rule_ids, or the ids the LLM judge cites in its reason."""
    if judge.get("rule_ids"):
        return list(judge["rule_ids"])
    return sorted(set(RULE_ID.findall(judge.get("text") or ""))) or ["(none)"]


def load_runs(run_dir: Path) -> list[dict]:
    runs = []
    for path in live_run_files(run_dir):
        run = json.loads(path.read_text())
        if run.get("error") or run.get("valid") is False:
            continue
        runs.append(run)
    return runs


def fig_main(sig: dict, conditions: list[str], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2))
    width = 0.8 / len(conditions)
    for i, c in enumerate(conditions):
        rates, lo, hi = [], [], []
        for key, _ in METRICS:
            m = sig["conditions"][c][key]
            rates.append(100 * m["rate"])
            lo.append(100 * (m["rate"] - m["ci95"][0]))
            hi.append(100 * (m["ci95"][1] - m["rate"]))
        xs = [j + (i - (len(conditions) - 1) / 2) * width for j in range(len(METRICS))]
        bars = ax.bar(xs, rates, width, yerr=[lo, hi], capsize=3, color=COLOR[c], label=LABEL[c])
        for bar, value, up in zip(bars, rates, hi):
            ax.text(bar.get_x() + bar.get_width() / 2, value + up + 1.5, f"{value:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(METRICS)), [name for _, name in METRICS])
    ax.set_ylabel("%")
    ax.set_ylim(0, 105)
    ax.set_title("Main metrics by condition (95% Wilson CI)")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def per_suite(runs: list[dict], conditions: list[str]) -> dict:
    table = defaultdict(lambda: defaultdict(lambda: {"asr": [0, 0], "utility": [0, 0], "uua": [0, 0]}))
    for r in runs:
        cell = table[r["suite"]][r["condition"]]
        if r["injection_task"] is None:
            cell["utility"][0] += bool(r["utility"])
            cell["utility"][1] += 1
        else:
            cell["asr"][0] += bool(r["security"])
            cell["asr"][1] += 1
            cell["uua"][0] += bool(r["utility"])
            cell["uua"][1] += 1
    return table


def fig_suites(table: dict, conditions: list[str], out: Path) -> None:
    suites = sorted(table)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    width = 0.8 / len(conditions)
    for ax, (key, title) in zip(axes, [("asr", "ASR by suite"), ("utility", "Benign utility by suite")]):
        for i, c in enumerate(conditions):
            vals = [100 * table[s][c][key][0] / table[s][c][key][1] if table[s][c][key][1] else 0 for s in suites]
            xs = [j + (i - (len(conditions) - 1) / 2) * width for j in range(len(suites))]
            ax.bar(xs, vals, width, color=COLOR[c], label=LABEL[c])
        ax.set_xticks(range(len(suites)), suites)
        ax.set_ylim(0, 105)
        ax.set_title(title)
        ax.set_ylabel("%")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def fig_tradeoff(sig: dict, conditions: list[str], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 4.2))
    for c in conditions:
        asr = sig["conditions"][c]["asr"]
        uua = sig["conditions"][c]["utility_under_attack"]
        ax.errorbar(100 * asr["rate"], 100 * uua["rate"],
                    xerr=[[100 * (asr["rate"] - asr["ci95"][0])], [100 * (asr["ci95"][1] - asr["rate"])]],
                    yerr=[[100 * (uua["rate"] - uua["ci95"][0])], [100 * (uua["ci95"][1] - uua["rate"])]],
                    fmt="o", color=COLOR[c], capsize=3, label=LABEL[c])
    ax.set_xlabel("ASR (%)  ← better")
    ax.set_ylabel("Utility under attack (%)  better →")
    ax.set_title("Security–utility trade-off")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def rule_counts(runs: list[dict], condition: str) -> tuple[Counter, Counter]:
    """BLOCK rule ids of effect calls, split by attack runs and benign runs."""
    attack, benign = Counter(), Counter()
    for r in runs:
        if r["condition"] != condition:
            continue
        for e in r["trace"]:
            judge = e.get("judge") or {}
            if judge.get("verdict") != "BLOCK":
                continue
            target = benign if r["injection_task"] is None else attack
            for rid in judge_rule_ids(judge):
                target[rid] += 1
    return attack, benign


def fig_rules(runs: list[dict], conditions: list[str], out: Path) -> bool:
    gated = [c for c in conditions if c != "no_gate"]
    if not gated:
        return False
    fig, axes = plt.subplots(1, len(gated), figsize=(5.2 * len(gated), 3.8), squeeze=False)
    for ax, c in zip(axes[0], gated):
        attack, benign = rule_counts(runs, c)
        keys = [k for k in RULES + ["(none)"] if attack.get(k) or benign.get(k)]
        xs = range(len(keys))
        ax.bar([x - 0.2 for x in xs], [attack.get(k, 0) for k in keys], 0.4, color="#d93025", label="attack runs")
        ax.bar([x + 0.2 for x in xs], [benign.get(k, 0) for k in keys], 0.4, color="#188038", label="benign runs")
        ax.set_xticks(list(xs), keys)
        ax.set_title(f"BLOCK by rule: {LABEL[c]}" + (" (cited in reason)" if c == "gate_llm_judge" else ""))
        ax.set_ylabel("blocked calls")
        ax.legend(frameon=False, fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return True


def fig_contrast(contrast: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 3.8))
    groups = [("attack_block", "Attack calls blocked\n(higher is better)"),
              ("legit_block", "Legit calls blocked\n(lower is better)")]
    for i, method in enumerate(["llm", "rules"]):
        color = COLOR["gate_llm_judge" if method == "llm" else "gate_static_rules"]
        label = LABEL["gate_llm_judge" if method == "llm" else "gate_static_rules"]
        vals, errs = [], [[], []]
        for key, _ in groups:
            k, n = contrast[method][key]
            rate = k / n if n else 0
            lo, hi = wilson(k, n) or (0, 0)
            vals.append(100 * rate)
            errs[0].append(100 * (rate - lo))
            errs[1].append(100 * (hi - rate))
        xs = [j + (i - 0.5) * 0.38 for j in range(len(groups))]
        bars = ax.bar(xs, vals, 0.38, yerr=errs, capsize=3, color=color, label=label)
        for bar, value, up in zip(bars, vals, errs[1]):
            ax.text(bar.get_x() + bar.get_width() / 2, value + up + 1.5, f"{value:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(groups)), [name for _, name in groups])
    ax.set_ylim(0, 110)
    ax.set_ylabel("% of effect calls")
    ax.set_title("Replay contrast: same inputs, per call")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--contrast", default=None, help="replay_contrast.py --json output (optional)")
    parser.add_argument("--out", default=str(FIG_DIR))
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sig = json.loads((run_dir / "significance.json").read_text())
    conditions = [c for c in ORDER if c in sig["conditions"]]
    runs = load_runs(run_dir)
    written = []
    fig_main(sig, conditions, out / "main_metrics.png")
    written.append("main_metrics.png")
    fig_suites(per_suite(runs, conditions), conditions, out / "by_suite.png")
    written.append("by_suite.png")
    fig_tradeoff(sig, conditions, out / "tradeoff.png")
    written.append("tradeoff.png")
    if fig_rules(runs, conditions, out / "rules.png"):
        written.append("rules.png")
    if args.contrast and Path(args.contrast).exists():
        fig_contrast(json.loads(Path(args.contrast).read_text()), out / "replay_contrast.png")
        written.append("replay_contrast.png")
    print("wrote " + ", ".join(str(out / name) for name in written))


if __name__ == "__main__":
    main()
