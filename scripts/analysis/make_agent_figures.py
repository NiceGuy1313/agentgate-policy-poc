"""에이전트(프레임워크)마다 그래프 하나씩을 results/figures/agent_<name>.png로 그리고 RESULTS.md에 붙인다.

입력은 집계 JSON뿐이다(원시 실행 기록 불필요): native는 results/summary/significance.json(전체 규모),
팀원 Wrapper 프레임워크는 results/summary/frameworks.json(층화 표본). 각 그래프는 ASR·정상 Utility·공격 중 Utility를
세 조건(Gate 없음, 판정 LLM + 룰북, 룰북 정적 코드)으로 나란히 보이고 95% Wilson 신뢰구간을 표시한다.
사용법: python scripts/analysis/make_agent_figures.py
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402

RESULTS = PROJECT_ROOT / "results" / "RESULTS.md"
SUMMARY = PROJECT_ROOT / "results" / "summary"
FIG_DIR = PROJECT_ROOT / "results" / "figures"
SECTION = "## 에이전트별 그래프"
CONDITIONS = ["no_gate", "gate_llm_judge", "gate_static_rules"]
LABEL = {"no_gate": "No gate", "gate_llm_judge": "LLM Judge(Policy)", "gate_static_rules": "Static rules (regex)"}
COLOR = {"no_gate": "#9aa0a6", "gate_llm_judge": "#e8710a", "gate_static_rules": "#1a73e8"}
METRICS = [("asr", "ASR\n(lower is better)"), ("utility", "Utility\n(benign)"), ("uua", "Utility\nunder attack")]


def load_agents() -> dict[str, dict]:
    """{agent: {"sample": str, "table": {condition: {metric: {k, n, rate, ci95}}}}}"""
    agents = {}
    sig = json.loads((SUMMARY / "significance.json").read_text())["conditions"]
    agents["native"] = {"sample": "full scale (97 benign + 949 attack per condition)",
                        "table": {c: {"asr": sig[c]["asr"], "utility": sig[c]["utility"],
                                      "uua": sig[c]["utility_under_attack"]} for c in CONDITIONS}}
    frameworks = SUMMARY / "frameworks.json"
    if frameworks.exists():
        data = json.loads(frameworks.read_text())
        for name, table in data["table"].items():
            agents[name] = {"sample": "stratified sample (97 benign + 291 attack per condition)",
                            "table": {c: table[c] for c in CONDITIONS}}
    return agents


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


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    agents = load_agents()
    lines = [SECTION, "",
             "> `scripts/analysis/make_agent_figures.py`가 집계 JSON(`results/summary/`)에서 생성한다. 에이전트마다 세 조건의 "
             "ASR·정상 Utility·공격 중 Utility와 95% Wilson 신뢰구간이다. native는 전체 규모, 나머지는 층화 표본이다.", ""]
    for agent, info in agents.items():
        name = f"agent_{agent}.png"
        draw(agent, info, FIG_DIR / name)
        lines += [f"**{agent}**", "", f"![{agent}](figures/{name})", ""]
    text = RESULTS.read_text()
    block = "\n".join(lines) + "\n"
    if SECTION in text:
        start = text.index(SECTION)
        end = text.find("\n## ", start + len(SECTION))
        text = text[:start] + block + ("" if end == -1 else text[end + 1:])
    else:
        anchor = "## 실험 흐름"
        text = text.replace(anchor, block + "\n" + anchor, 1)
    RESULTS.write_text(text)
    print("wrote " + ", ".join(f"figures/agent_{a}.png" for a in agents))


if __name__ == "__main__":
    main()
