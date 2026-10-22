"""정책 준수율 보고서(COMPLIANCE.md)의 그림 두 개를 그린다.

compliance_decisions.png: 에이전트·판정 방식별 판정 결과 구성(위반 차단 / 위반 허용 / 정상 허용 / 정상 차단)
compliance_link.png: 준수율 사례로 본 예상 잔여 ASR·Utility 손실과 live 실측값(frameworks.json) 비교
입력: results/summary/compliance_calls_<agent>.json·compliance_labels_<agent>.json, compliance_link.json(compliance_review.py link)
사용법: python scripts/analysis/make_compliance_figures.py
"""

from __future__ import annotations

import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))
import compliance_review as cr  # noqa: E402

FIG_DIR = PROJECT_ROOT / "results" / "figures"
JUDGE_LABEL = {"A": "LLM Judge (Policy)", "B": "Static Checker (Policy)"}
JUDGE_COLOR = {"A": "#e8710a", "B": "#1a73e8"}
PARTS = [("violation blocked", "#1e8e3e"), ("violation allowed", "#d93025"),
         ("normal allowed", "#a8dab5"), ("normal blocked", "#f6aea9")]


def decisions(rows: list[dict], j: str) -> list[int]:
    effect = [r for r in rows if r["effect"]]
    v = [r for r in effect if r["label"] == "V"]
    c = [r for r in effect if r["label"] == "C"]
    return [sum(r[j] == "BLOCK" for r in v), sum(r[j] != "BLOCK" for r in v),
            sum(r[j] != "BLOCK" for r in c), sum(r[j] == "BLOCK" for r in c)]


def draw_decisions(out) -> None:
    bars, labels = [], []
    for agent in cr.SIMPLE_AGENTS:
        p = cr.paths(agent)
        rows = cr.load_rows(None, json.loads(p["labels"].read_text()), p["calls"])[0]
        for j, _ in cr.JUDGES:
            bars.append(decisions(rows, j))
            labels.append(f"{agent}\n{JUDGE_LABEL[j]}")
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ys = range(len(bars))
    left = [0] * len(bars)
    for k, (name, color) in enumerate(PARTS):
        widths = [b[k] for b in bars]
        ax.barh(ys, widths, left=left, color=color, label=name, height=0.6)
        for y, x0, w in zip(ys, left, widths):
            if w >= 4:
                ax.text(x0 + w / 2, y, str(w), ha="center", va="center", fontsize=8)
        left = [a + b for a, b in zip(left, widths)]
    for y, b in zip(ys, bars):
        ok = b[0] + b[2]
        ax.text(sum(b) + 2, y, f"{100 * ok / sum(b):.1f}%", va="center", fontsize=9, fontweight="bold")
    ax.set_yticks(list(ys), labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Tool calls (bold: policy compliance rate)")
    ax.set_xlim(0, max(sum(b) for b in bars) * 1.15)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def draw_link(out) -> None:
    data = json.loads(cr.LINK_JSON.read_text())
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    groups = [(a, j) for a in cr.SIMPLE_AGENTS for j, _ in cr.JUDGES]
    names = [f"{a}\n{JUDGE_LABEL[j].replace(' (Policy)', '')}" for a, j in groups]
    width = 0.36
    panels = [
        ("Residual ASR", lambda c: 100 * (c.get("attack_call_allowed", 0) + c.get("attack_answer_only", 0)) / c["n_attack"],
         lambda c: 100 * c["live_asr"]["rate"]),
        ("Utility loss", lambda c: 100 * c.get("benign_false_block", 0) / c["n_benign"],
         lambda c: -100 * c["live_utility_diff"]),
    ]
    for ax, (title, expected, measured) in zip(axes, panels):
        cells = [data[a][dict(cr.JUDGES)[j]] for a, j in groups]
        e = [expected(c) for c in cells]
        m = [measured(c) for c in cells]
        xs = range(len(groups))
        b1 = ax.bar([x - width / 2 for x in xs], e, width, color="#9aa0a6")
        b2 = ax.bar([x + width / 2 for x in xs], m, width, color=[JUDGE_COLOR[j] for _, j in groups])
        top = max(e + m)
        for bar, v in list(zip(b1, e)) + list(zip(b2, m)):
            ax.text(bar.get_x() + bar.get_width() / 2, v + top * 0.02, f"{v:.1f}", ha="center", fontsize=8)
        ax.set_xticks(list(xs), names, fontsize=8)
        ax.set_ylabel("%" if title == "Residual ASR" else "%p (Utility loss vs No Gate)")
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, top * 1.35)
        ax.spines[["top", "right"]].set_visible(False)
        handles = [Patch(color="#9aa0a6", label="Expected from compliance cases (80 tasks)")] + [
            Patch(color=JUDGE_COLOR[j], label=f"Measured, {JUDGE_LABEL[j]} (live, 388 tasks)") for j in ("A", "B")]
        ax.legend(handles=handles, frameon=False, fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    draw_decisions(FIG_DIR / "compliance_decisions.png")
    draw_link(FIG_DIR / "compliance_link.png")
    print("wrote figures/compliance_decisions.png, figures/compliance_link.png")


if __name__ == "__main__":
    main()
