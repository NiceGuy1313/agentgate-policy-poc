"""여러 에이전트 프레임워크의 실행 결과를 비교해 RESULTS.md의 "## 프레임워크별 결과" 절과 그림을 만든다.

입력: 프레임워크마다 같은 표본(--pairs)으로 돌린 실행 디렉터리(results/runs/<name>, 기록의 framework 필드로 구분).
출력: results/RESULTS.md의 해당 절(있으면 교체, 없으면 "## 실험 흐름" 앞에 넣음), results/figures/frameworks.png,
      results/summary/frameworks.json. 수치는 모두 실행 기록에서 계산한다(Gate 우회로 무효인 run은 제외).
사용법: python scripts/analysis/summarize_frameworks.py --runs results/runs/strat_native results/runs/strat_smolagents ...
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

RESULTS = PROJECT_ROOT / "results" / "RESULTS.md"
SECTION = "## 프레임워크별 결과(층화 표본)"
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
    (summary_dir / "frameworks.json").write_text(json.dumps(
        {"sample": args.pairs, "skipped": dict(skipped), "table": table}, indent=1, ensure_ascii=False))

    def cell(f, c, m):
        x = table[f][c][m]
        return f"{100 * x['rate']:.1f}% ({x['k']}/{x['n']})"
    n_attack = table[frameworks[0]][CONDITIONS[0]]["asr"]["n"]
    n_benign = table[frameworks[0]][CONDITIONS[0]]["utility"]["n"]
    lines = [SECTION, "",
             f"> `scripts/analysis/summarize_frameworks.py`가 생성한다. 팀원 Wrapper가 씌워진 프레임워크({', '.join(frameworks)})를 "
             f"같은 층화 표본(`{args.pairs}`, 정상 {n_benign}건 · 공격 {n_attack}건, 작업마다 공격 3개)으로 돌렸다. "
             "에이전트·LLM Judge는 모두 `ollama:gpt-oss:120b`, 공격은 `tool_knowledge`다. 모든 툴 실행은 Adapter → Bridge → "
             "Policy Gate를 지난다. native 결과는 위 \"결과\" 절의 전체 규모 실험(조건마다 정상 97건 · 공격 949건)이다.", "",
             f"제외한 run: 오류 {skipped.get('error', 0)}건, Gate 우회로 무효 {skipped.get('invalid', 0)}건. "
             "Ollama 서버 오류(500·429)로 실패한 run은 다시 실행해 채웠고, 남은 오류는 AgentDojo 채점 함수가 에이전트 호출에 없는 "
             "인자를 읽다 실패한 경우다.", "",
             "| 프레임워크 | 조건 | ASR | Utility | Utility under Attack | 차단 호출 |", "|---|---|---|---|---|---|"]
    for f in frameworks:
        for c in CONDITIONS:
            lines.append(f"| {f} | {NAMES[c]} | {cell(f, c, 'asr')} | {cell(f, c, 'utility')} | {cell(f, c, 'uua')} | "
                         f"{table[f][c]['blocked']} |")
    lines += ["", "같은 (사용자 작업, 공격) 짝끼리 비교한 차이(exact McNemar p):", ""]
    for f in frameworks:
        parts = []
        for c in ("gate_llm_judge", "gate_static_rules"):
            parts.append(f"{NAMES[c]} − No Gate: ASR {diff_text(paired(runs, f, c, 'no_gate', 'asr'))}, "
                         f"Utility {diff_text(paired(runs, f, c, 'no_gate', 'utility'))}, "
                         f"Utility under Attack {diff_text(paired(runs, f, c, 'no_gate', 'uua'))}")
        parts.append("LLM Judge − Static Checker: ASR "
                     f"{diff_text(paired(runs, f, 'gate_llm_judge', 'gate_static_rules', 'asr'))}, Utility "
                     f"{diff_text(paired(runs, f, 'gate_llm_judge', 'gate_static_rules', 'utility'))}, Utility under Attack "
                     f"{diff_text(paired(runs, f, 'gate_llm_judge', 'gate_static_rules', 'uua'))}")
        lines.append(f"- **{f}**")
        lines += [f"  - {p}" for p in parts]
    lines += ["", "**프레임워크 × 검증 방식(95% Wilson 신뢰구간)**", "", "![프레임워크별 결과](figures/frameworks.png)", ""]

    text = RESULTS.read_text() if RESULTS.exists() else "# 결과\n\n"
    block = "\n".join(lines) + "\n"
    if SECTION in text:
        start = text.index(SECTION)
        end = text.find("\n## ", start + len(SECTION))
        text = text[:start] + block + ("" if end == -1 else text[end + 1:])
    else:
        anchor = "## 실험 흐름"
        text = text.replace(anchor, block + "\n" + anchor, 1) if anchor in text else text + "\n" + block
    RESULTS.write_text(text)
    print(f"wrote {RESULTS}, {fig_dir / 'frameworks.png'}, {summary_dir / 'frameworks.json'}")


if __name__ == "__main__":
    main()
