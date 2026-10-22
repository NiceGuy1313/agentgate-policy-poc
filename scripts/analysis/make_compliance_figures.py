"""정책 준수율 보고서(COMPLIANCE.md)의 그림 compliance_link.png를 그린다.

판정 사례 기반 잔여 ASR·Utility 손실(compliance_review.py link)과 Gate 실행 결과(frameworks.json)를 나란히 비교한다.
입력: results/summary/compliance_link.json
사용법: python scripts/analysis/make_compliance_figures.py
"""

from __future__ import annotations

import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.config import PROJECT_ROOT  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))
import compliance_review as cr  # noqa: E402

FIG_DIR = PROJECT_ROOT / "results" / "figures"
JUDGMENT, GATE = "#bdc1c6", "#1a73e8"


def draw_link(out) -> None:
    data = json.loads(cr.LINK_JSON.read_text())
    groups = [(a, n) for a in cr.SIMPLE_AGENTS for _, n in cr.JUDGES]
    names = [f"{a}\n{n}" for a, n in groups]
    panels = [
        ("Residual ASR (%)",
         lambda c: 100 * (c.get("attack_call_allowed", 0) + c.get("attack_answer_only", 0)) / c["n_attack"],
         lambda c: 100 * c["live_asr"]["rate"]),
        ("Utility loss (%p)",
         lambda c: 100 * c.get("benign_false_block", 0) / c["n_benign"],
         lambda c: -100 * c["live_utility_diff"]),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    width = 0.38
    for ax, (title, judged, gate) in zip(axes, panels):
        cells = [data[a][n] for a, n in groups]
        j = [judged(c) for c in cells]
        g = [gate(c) for c in cells]
        xs = range(len(groups))
        b1 = ax.bar([x - width / 2 for x in xs], j, width, color=JUDGMENT, label="Judgment-based")
        b2 = ax.bar([x + width / 2 for x in xs], g, width, color=GATE, label="Gate run")
        top = max(j + g)
        for bar, v in list(zip(b1, j)) + list(zip(b2, g)):
            ax.text(bar.get_x() + bar.get_width() / 2, v + top * 0.02, f"{v:.1f}", ha="center", fontsize=9)
        ax.set_xticks(list(xs), names, fontsize=8)
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, top * 1.25)
        ax.set_yticks([])
        ax.spines[["top", "right", "left"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=9, loc="upper right")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    draw_link(FIG_DIR / "compliance_link.png")
    print("wrote figures/compliance_link.png")


if __name__ == "__main__":
    main()
