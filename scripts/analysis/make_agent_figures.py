"""Agent(smolagents, mini-swe-agent)별 그래프(agent_<name>.png)와 전체 그래프(frameworks.png)를 그린다(보고서는 write_report.py).

입력은 집계 JSON results/summary/frameworks.json뿐이다(원시 실행 기록 불필요). 각 그래프는 ASR·Utility·Utility under Attack을
세 조건(No Gate, LLM Judge (Policy), Static Checker (Policy))으로 나란히 보이고 95% Wilson 신뢰구간을 표시한다.
사용법: python scripts/analysis/make_agent_figures.py
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402

SUMMARY = PROJECT_ROOT / "results" / "summary"
FIG_DIR = PROJECT_ROOT / "results" / "figures"
CONDITIONS = ["no_gate", "gate_llm_judge", "gate_static_rules"]
LABEL = {"no_gate": "No Gate", "gate_llm_judge": "LLM Judge (Policy)", "gate_static_rules": "Static Checker (Policy)"}
COLOR = {"no_gate": "#9aa0a6", "gate_llm_judge": "#e8710a", "gate_static_rules": "#1a73e8"}
METRICS = [("asr", "ASR"), ("utility", "Utility"), ("uua", "Utility under Attack")]


def load_agents() -> dict[str, dict]:
    """{agent: {"table": {condition: {metric: {k, n, rate, ci95}}}}} for the two Wrapper agents."""
    data = json.loads((SUMMARY / "frameworks.json").read_text())
    return {name: {"table": {c: table[c] for c in CONDITIONS}} for name, table in data["table"].items()}


def draw(agent: str, info: dict, out) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    width = 0.26
    for i, c in enumerate(CONDITIONS):
        cells = [info["table"][c][m] for m, _ in METRICS]
        rates = [100 * x["rate"] for x in cells]
        lo = [100 * (x["rate"] - x["ci95"][0]) for x in cells]
        hi = [100 * (x["ci95"][1] - x["rate"]) for x in cells]
        xs = [j + (i - 1) * width for j in range(len(METRICS))]
        bars = ax.bar(xs, rates, width, yerr=[lo, hi], capsize=3, color=COLOR[c], label=LABEL[c])
        for bar, value, up in zip(bars, rates, hi):
            ax.text(bar.get_x() + bar.get_width() / 2, value + up + 1.5, f"{value:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(METRICS)), [name for _, name in METRICS])
    ax.set_ylim(0, 108)
    ax.set_ylabel("%")
    ax.set_title(agent, fontsize=11)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def draw_overview(agents: dict, out) -> None:
    """One panel per metric, Agents on the x axis, three conditions side by side."""
    names = list(agents)
    fig, axes = plt.subplots(1, len(METRICS), figsize=(13, 4.2))
    width = 0.26
    for ax, (metric, title) in zip(axes, METRICS):
        for i, c in enumerate(CONDITIONS):
            cells = [agents[a]["table"][c][metric] for a in names]
            rates = [100 * x["rate"] for x in cells]
            lo = [100 * (x["rate"] - x["ci95"][0]) for x in cells]
            hi = [100 * (x["ci95"][1] - x["rate"]) for x in cells]
            xs = [j + (i - 1) * width for j in range(len(names))]
            bars = ax.bar(xs, rates, width, yerr=[lo, hi], capsize=3, color=COLOR[c], label=LABEL[c])
            for bar, value, up in zip(bars, rates, hi):
                ax.text(bar.get_x() + bar.get_width() / 2, value + up + 1.5, f"{value:.1f}", ha="center", fontsize=8)
        ax.set_xticks(range(len(names)), names)
        ax.set_ylim(0, 108)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("%")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    agents = load_agents()
    for agent, info in agents.items():
        draw(agent, info, FIG_DIR / f"agent_{agent}.png")
    draw_overview(agents, FIG_DIR / "frameworks.png")
    print("wrote " + ", ".join(f"figures/agent_{a}.png" for a in agents) + ", figures/frameworks.png")


if __name__ == "__main__":
    main()
