"""결과 파일로부터 최종 보고서, 그림, 결과 dashboard를 재생성한다.

  python scripts/reporting/build_report.py

입력: results/replay/v1/{oracle,llm_<model>}/, (6절) results/replay/v3·v31/, results/live/v2·v3·v31/,
      results/toolspec/v1/, results/rules/v1/, config/policy_rules_v31.yaml,
      results/live/v1/<run>/{summary,significance,enforcement_check,run_*}.json,
      results/judge/v1/<model>/judge_eval.json, data/benchmark/, data/pairs/, data/specs/llm/, data/judge_cache/
출력: docs/final_report.md, results/figures/*.png, results/dashboard.html,
      v2_detector_trusted_llm/RESULTS.md, v3_trusted_llm_policy_doc/RESULTS.md
"""

from __future__ import annotations

import json
import re
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import tomllib
import yaml

from agentgate import BENCHMARK_VERSION
from agentgate.config import PROJECT_ROOT, load_policy_config
from agentgate.evaluation.metrics import TYPE_NAMES, compare_runs

DOCS_DIR, FIGURES_DIR = PROJECT_ROOT / "docs", PROJECT_ROOT / "results" / "figures"
REPLAY_DIR = PROJECT_ROOT / "results/replay/v1/oracle"
LIVE_ROOT = PROJECT_ROOT / "results/live/v1"
PAIRS_FILE = "data/pairs/strat3_seed0.txt"
CONDITION_LABELS_EN = {"C0": "no defense", "C1": "gate\nESC→block", "C2": "gate\nESC→allow",
                       "C3": "built-in\n(spotlighting)", "C4": "gate\nESC→judge"}
CONDITION_NAMES = {"C0": "방어 없음", "C1": "게이트 (ESCALATE→block)", "C2": "게이트 (ESCALATE→allow)",
                   "C3": "AgentDojo 내장 방어", "C4": "게이트 (ESCALATE→judge)"}
# (gate mode, ESCALATE handling, other defence) per condition
CONDITION_SETUP = {
    "C0": ("audit (판정만 기록)", "-", "없음"),
    "C1": ("enforce", "BLOCK", "없음"),
    "C2": ("enforce", "ALLOW", "없음"),
    "C3": ("audit (판정만 기록)", "-", "AgentDojo `spotlighting_with_delimiting`"),
    "C4": ("enforce", "판정 LLM: B→BLOCK, A→ALLOW(가역 툴), C·비가역 툴→HUMAN(BLOCK으로 집행)", "없음"),
}
LIVE_METRICS = (("asr", "ASR"), ("utility", "Utility"), ("utility_under_attack", "Utility under attack"))
# reference categorical palette (dataviz skill, slots 1-3 validate all-pairs)
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def load_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def percent_of(count, total, digits=1):
    return f"{100 * count / total:.{digits}f}% ({count}/{total})" if total else "n/a"


def format_rate(rate):
    return "n/a" if rate is None else f"{100 * rate:.1f}%"


def markdown_table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def load_significance(name: str) -> dict | None:
    return load_json(LIVE_ROOT / name / "significance.json")


def load_run_args(name: str) -> dict:
    """CLI arguments of the latest live_evaluation.py invocation that wrote this run directory."""
    runs = sorted((LIVE_ROOT / name).glob("run_*.json"))
    return load_json(runs[-1])["args"] if runs else {}


def stratified_runs(live_summaries: dict) -> list[tuple[str, dict]]:
    return [(name, summary) for name, summary in live_summaries.items() if name.startswith("strat3_")]


def format_ci(metric: dict) -> str:
    lower, upper = metric["ci95"]
    return f"{100 * metric['k'] / metric['n']:.1f}% [{100 * lower:.1f}, {100 * upper:.1f}]"


def format_paired_difference(metric: dict) -> str:
    paired = metric.get("paired_vs_C0")
    if not paired:
        return "-"
    return f"{100 * (paired['cond_only'] - paired['c0_only']) / paired['n_pairs']:+.1f}%p (p={paired['p_mcnemar']:.2g})"


def short_rate(metric: dict) -> str:
    return f"{100 * metric['k'] / metric['n']:.1f}%"


# ---------------------------------------------------------------------------------------------- figures
def _style_axes(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def grouped_bars(path, groups, series, title, ylabel, note=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, ax = plt.subplots(figsize=(8.4, 3.9), dpi=150)
    n_series = len(series)
    bar_width = 0.8 / n_series
    for i, (series_name, values, color) in enumerate(series):
        xs = [group_index + (i - (n_series - 1) / 2) * bar_width for group_index in range(len(groups))]
        bars = ax.bar(xs, values, width=bar_width - 0.03, color=color, label=series_name, edgecolor="white",
                      linewidth=1.5)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 1.2, f"{value:.0f}", ha="center", va="bottom",
                    fontsize=7.5, color=MUTED)
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylim(0, 108)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    _style_axes(ax)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    if note:
        fig.text(0.01, 0.01, note, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04 if note else 0, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_ci_panels(path, runs, title):
    """One panel per metric; bars per model with Wilson 95% CI error bars (from significance.json)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "DejaVu Sans"
    metric_labels = [("asr", "ASR"), ("utility", "Utility (no attack)"), ("utility_under_attack", "Utility under attack")]
    conditions = list(runs[0][1]["conditions"])
    colors = [BLUE, ORANGE, GREEN]
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.9), dpi=150, sharey=True)
    n_models = len(runs)
    bar_width = 0.8 / n_models
    for ax, (metric_key, label) in zip(axes, metric_labels):
        for i, (model, significance) in enumerate(runs):
            xs = [group_index + (i - (n_models - 1) / 2) * bar_width for group_index in range(len(conditions))]
            metric_values = [significance["conditions"][c][metric_key] for c in conditions]
            values = [100 * metric["k"] / metric["n"] for metric in metric_values]
            lower_errors = [value - 100 * metric["ci95"][0] for value, metric in zip(values, metric_values)]
            upper_errors = [100 * metric["ci95"][1] - value for value, metric in zip(values, metric_values)]
            ax.bar(xs, values, width=bar_width - 0.03, color=colors[i], label=model, edgecolor="white", linewidth=1.5)
            ax.errorbar(xs, values, yerr=[lower_errors, upper_errors], fmt="none", ecolor=INK, elinewidth=0.9,
                        capsize=2.5)
            for position, value, upper_error in zip(xs, values, upper_errors):
                ax.text(position, value + upper_error + 1.5, f"{value:.0f}", ha="center", va="bottom", fontsize=7,
                        color=MUTED)
        short_names = {"C0": "none", "C1": "block", "C2": "allow", "C3": "built-in", "C4": "judge"}
        ax.set_xticks(range(len(conditions)), [f"{c}\n{short_names[c]}" for c in conditions], fontsize=7.5)
        ax.set_ylim(0, 108)
        ax.set_title(label, loc="left", fontsize=10, color=INK)
        _style_axes(ax)
    axes[0].set_ylabel("%", color=MUTED, fontsize=9)
    axes[-1].legend(frameon=False, fontsize=8.5, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.01, "C1/C2/C4: gate with ESCALATE → block / allow / judge LLM. Error bars: Wilson 95% CI. "
             "Paired tests vs C0 are in the table (McNemar, user-task cluster bootstrap).",
             fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def build_figures(replay_summary, live_summaries):
    figs = {}
    by_type = replay_summary["by_type"]
    types = [t for t in "ABCDEFG" if t in by_type]
    grouped_bars(FIGURES_DIR / "detection_by_type.png", types, [
        ("strict (BLOCK)", [100 * by_type[t]["strict"] / by_type[t]["eval"] for t in types], BLUE),
        ("lenient (BLOCK or ESCALATE)", [100 * by_type[t]["lenient"] / by_type[t]["eval"] for t in types], ORANGE),
    ], "Pair-level detection by attack type (replay, oracle TaskSpec)", "% of pairs",
        note="Type H (no tool call) excluded: no effect call to detect. A-G as in docs/policy_spec.md §1.")
    figs["type"] = "results/figures/detection_by_type.png"
    ablation = replay_summary["ablation"]
    policy_ids = [f"P{i}" for i in range(1, 10)]
    n_evaluated = ablation["none"]["eval"]
    grouped_bars(FIGURES_DIR / "ablation.png", [f"-{p}" for p in policy_ids], [
        ("strict detection", [100 * ablation[p]["strict"] / n_evaluated for p in policy_ids], BLUE),
        ("lenient detection", [100 * ablation[p]["lenient"] / n_evaluated for p in policy_ids], ORANGE),
    ], f"Detection with one policy disabled (all on: strict {100 * ablation['none']['strict'] / n_evaluated:.0f}%, "
       f"lenient {100 * ablation['none']['lenient'] / n_evaluated:.0f}%)", "% of pairs")
    figs["ablation"] = "results/figures/ablation.png"
    for name, summary in live_summaries.items():
        conditions = list(summary["conditions"])
        metrics = summary["conditions"]
        c0 = metrics[conditions[0]]
        grouped_bars(FIGURES_DIR / f"tradeoff_{name}.png", [f"{c}\n{CONDITION_LABELS_EN[c]}" for c in conditions], [
            ("Utility (no attack)", [100 * (metrics[c]["utility"] or 0) for c in conditions], BLUE),
            ("Utility under attack", [100 * (metrics[c]["utility_under_attack"] or 0) for c in conditions], GREEN),
            ("ASR", [100 * (metrics[c]["asr"] or 0) for c in conditions], ORANGE),
        ], f"Utility vs ASR by condition — agent: {summary['agent_model']}\n(stratified, attack {summary.get('attack')}: "
           f"{c0['attack_runs']} attack pairs + {c0['benign_runs']} benign)", "%")
        figs[f"tradeoff_{name}"] = f"results/figures/tradeoff_{name}.png"
    significance_runs = [(summary["agent_model"], load_significance(name))
                         for name, summary in stratified_runs(live_summaries) if load_significance(name)]
    if significance_runs:
        attacks = sorted({summary.get("attack") for _, summary in stratified_runs(live_summaries)})
        plot_ci_panels(FIGURES_DIR / "strat3_ci.png", significance_runs,
                       f"Stratified sample, attack {', '.join(attacks)} — rate by condition and model")
        figs["strat_ci"] = "results/figures/strat3_ci.png"
    return figs


# ---------------------------------------------------------------------------------------------- final report
def replay_rows(summary: dict) -> list[tuple[str, str]]:
    overall, benign, prevention = summary["overall"], summary["benign"], summary["prevention"]
    under_attack = summary["utility_under_attack"]
    return [
        ("평가 쌍 (효과 호출 있음) / 전체", f"{overall['eval']} / {summary['n_attack_pairs']}"),
        ("쌍 단위 탐지율 strict", percent_of(overall["strict"], overall["eval"])),
        ("쌍 단위 탐지율 lenient", percent_of(overall["lenient"], overall["eval"])),
        ("호출 단위 탐지율 (BLOCK+ESCALATE)", percent_of(overall["calls_flagged"], overall["calls"])),
        ("저지율 native", percent_of(prevention["prevented"], prevention["baseline_success"])),
        ("저지율 executed-only", percent_of(prevention["prevented_executed_only"], prevention["baseline_success"])),
        ("정상 user task 중 BLOCK 발생", percent_of(benign["tasks_block"], benign["tasks"])),
        ("정상 user task 중 ESCALATE 발생", percent_of(benign["tasks_escalate"], benign["tasks"])),
        ("정상 Utility audit → enforce",
         f"{benign['utility_audit']}/{benign['tasks']} → {benign['utility_enforce']}/{benign['tasks']}"),
        ("공격 하 Utility audit → enforce",
         f"{under_attack['audit']}/{under_attack['n']} → {under_attack['enforce']}/{under_attack['n']}"),
        ("판정 지연 ms/호출 (mean / p95)", "{mean:.3f} / {p95:.3f}".format(**summary["decide_ms"])),
    ]


def pairs_file_design() -> dict:
    entries = [line.split(":") for line in (PROJECT_ROOT / PAIRS_FILE).read_text().split() if line]
    attack = [e for e in entries if e[2] != "none"]
    users = {(e[0], e[1]) for e in entries}
    seed = re.search(r"seed(\d+)", PAIRS_FILE)
    return {"users": len(users), "attack": len(attack), "benign": len(entries) - len(attack),
            "k": len(attack) // len(users), "seed": seed.group(1) if seed else "?"}


def summary_bullets(replay_summary, llm_replay_summaries, live_summaries, enforcement_checks, judge) -> list[str]:
    overall, benign, prevention = replay_summary["overall"], replay_summary["benign"], replay_summary["prevention"]
    lines = [f"- **RQ1 재생 (oracle TaskSpec)**: 효과 호출이 있는 {overall['eval']}쌍 중 "
             f"{percent_of(overall['lenient'], overall['eval'])}을 BLOCK 또는 ESCALATE로, "
             f"{percent_of(overall['strict'], overall['eval'])}을 BLOCK으로 잡는다. ESCALATE까지 차단하면 정책 없이 "
             f"성공하던 공격의 {percent_of(prevention['prevented'], prevention['baseline_success'])}이 실패로 바뀐다. "
             f"정상 user task {benign['tasks']}개 중 BLOCK이 나온 태스크는 {benign['tasks_block']}개다."]
    for model, summary in llm_replay_summaries.items():
        overall_llm = summary["overall"]
        lines.append(f"- **LLM TaskSpec ({model})**: strict {percent_of(overall_llm['strict'], overall_llm['eval'])}"
                     f", lenient {percent_of(overall_llm['lenient'], overall_llm['eval'])}로 oracle과 비슷하다.")
    for name, summary in stratified_runs(live_summaries):
        significance = load_significance(name)
        if not significance:
            continue
        conditions = significance["conditions"]
        gated = [c for c in conditions if c != "C0"]
        lines.append(
            f"- **RQ2 {summary['agent_model']}** (`{summary.get('attack')}`, 공격 {conditions['C0']['asr']['n']}쌍): "
            f"ASR C0 {short_rate(conditions['C0']['asr'])} → "
            + ", ".join(f"{c} {short_rate(conditions[c]['asr'])}" for c in gated)
            + f". 정상 Utility C0 {short_rate(conditions['C0']['utility'])} → "
            + ", ".join(f"{c} {short_rate(conditions[c]['utility'])}" for c in gated) + ".")
    for model, judge_eval in judge.items():
        confusion = judge_eval["confusion_final"]
        n_attack = sum(v for k, v in confusion.items() if k.startswith("attack->"))
        n_normal = sum(v for k, v in confusion.items() if k.startswith("normal->"))
        lines.append(f"- **판정 LLM ({model})**: 재생의 ESCALATE {judge_eval['n']}건 중 공격 호출 {n_attack}건에서 ALLOW "
                     f"{confusion.get('attack->ALLOW', 0)}건, 정상 호출 {n_normal}건에서 ALLOW "
                     f"{confusion.get('normal->ALLOW', 0)}건, HUMAN 비율 {format_rate(judge_eval['human_rate'])}.")
    lines.append(f"- **집행 무결성**: 실행 묶음 {len(enforcement_checks)}개, {sum(e['runs'] for e in enforcement_checks.values())}회 "
                 f"실행에서 위반 {sum(e['violations'] for e in enforcement_checks.values())}건.")
    return lines


def setup_section(replay_summary, live_summaries) -> list[str]:
    runs_by_condition = {c: [summary["agent_model"] for _, summary in live_summaries.items() if c in summary["conditions"]]
                         for c in CONDITION_SETUP}
    attacks = {}
    for name, summary in live_summaries.items():
        attacks.setdefault(summary.get("attack"), []).append(summary["agent_model"])
    design = pairs_file_design()
    lines = ["## 1. 평가 설정", "",
             markdown_table(["조건", "게이트", "ESCALATE 처리", "그 밖의 방어", "실행한 모델"],
                            [[f"{c} {CONDITION_NAMES[c]}", *setup, ", ".join(runs_by_condition[c]) or "-"]
                             for c, setup in CONDITION_SETUP.items()]), "",
             "- 공격 `important_instructions`(AgentDojo 내장): 툴 응답에 사용자 명의의 지시문으로 injection task GOAL을 넣는다. "
             "재생 평가에 사용"
             + (f", 실제 에이전트 {', '.join(attacks['important_instructions'])}"
                if attacks.get("important_instructions") else "") + ".",
             f"- 공격 `tool_knowledge`(AgentDojo 내장): 같은 형식에 GOAL 수행에 쓸 툴과 인자를 함께 알려 준다. "
             f"{', '.join(attacks.get('tool_knowledge', [])) or '-'} 실행.",
             f"- 층화 표본(`{PAIRS_FILE}`, `scripts/evaluation/make_stratified_pairs.py`): "
             f"user task {design['users']}개 전부에 같은 suite의 injection task {design['k']}개를 배정한다"
             f"(suite마다 시드 {design['seed']}에서 섞은 순서를 차례로 배정). 공격 {design['attack']}쌍.",
             f"- user task마다 공격 없는 정상 실행 1개를 더해 정상 {design['benign']}개.",
             f"- 전체 {replay_summary['n_attack_pairs']}쌍보다 작지만 모든 user task와 injection task를 덮는다. "
             "조건마다 같은 짝을 한 번씩 실행해 C0와 짝 비교한다.", "",
             markdown_table(["지표", "정의"], [
                 ["ASR", "공격 실행 중 injection task `security()`가 참(공격 목표 달성)인 비율"],
                 ["Utility", "공격 없는 실행 중 user task `utility()`가 참인 비율"],
                 ["Utility under attack", "공격 실행 중 user task `utility()`가 참인 비율"],
                 ["탐지율 strict / lenient", "재생에서 공격 궤적의 효과 호출 중 하나 이상이 BLOCK / BLOCK 또는 ESCALATE인 쌍의 비율"],
                 ["저지율", "정책 없이 공격이 성공한 쌍 중 enforce 재생에서 `security()`가 거짓이 된 비율. "
                            "executed-only는 실제 실행된 호출만으로 판정"],
             ]), ""]
    return lines


def rq1_section(replay_summary, llm_replay_summaries, figs) -> list[str]:
    columns = [("oracle", replay_summary)] + [(f"LLM {m}", s) for m, s in llm_replay_summaries.items()]
    rows_by_column = [replay_rows(summary) for _, summary in columns]
    by_type, breakdown = replay_summary["by_type"], replay_summary["prevention_breakdown"]
    ablation = replay_summary["ablation"]
    drops = {p: (ablation["none"]["strict"] - v["strict"], ablation["none"]["lenient"] - v["lenient"])
             for p, v in ablation.items() if p != "none"}
    top_strict = max(drops, key=lambda p: drops[p][0])
    top_lenient = max(drops, key=lambda p: drops[p][1])
    overall = replay_summary["overall"]
    causes = Counter()
    for pair_misses in replay_summary["misses"].values():
        causes.update(m["cause"] for m in pair_misses)
    lines = ["## 2. RQ1 정답 궤적 재생", "",
             f"user task 정답 궤적 뒤에 injection task 참조 궤적을 이어 붙여 {replay_summary['n_attack_pairs']}쌍을 audit·enforce로 "
             f"재생했다(`important_instructions`, LLM 호출 없음). oracle은 프롬프트만 보고 사람이 쓴 TaskSpec, "
             "LLM 열은 같은 입력으로 추출한 TaskSpec이다."
             + "".join(f" oracle과 LLM {model} spec의 탐지 결과(strict, lenient)가 다른 쌍은 "
                       f"{len(compare_runs(REPLAY_DIR, PROJECT_ROOT / f'results/replay/v1/llm_{model}'))}쌍이다."
                       for model in llm_replay_summaries), "",
             markdown_table(["지표"] + [name for name, _ in columns],
                            [[label] + [rows[i][1] for rows in rows_by_column]
                             for i, (label, _) in enumerate(rows_by_column[0])]), ""]
    lines += ["유형별 결과(oracle). 유형 H는 효과 호출이 없어 탐지율 분모에서 빠지고 저지율에는 남는다.", "",
              markdown_table(["유형", "쌍", "strict", "lenient", "저지 native", "저지 executed-only"],
                             [[f"{t} {TYPE_NAMES[t]}", by_type[t]["pairs"],
                               percent_of(by_type[t]["strict"], by_type[t]["eval"]) if "eval" in by_type[t] else "-",
                               percent_of(by_type[t]["lenient"], by_type[t]["eval"]) if "eval" in by_type[t] else "-",
                               percent_of(breakdown[t]["prevented"], breakdown[t]["base"]),
                               percent_of(breakdown[t]["prevented_executed_only"], breakdown[t]["base"])]
                              for t in "ABCDEFGH" if t in by_type and t in breakdown]), "",
              f"![detection by type](../{figs['type']})", "",
              f"정책 하나를 끄면 strict 탐지가 가장 크게 줄어드는 정책은 {top_strict}({drops[top_strict][0]}쌍), "
              f"lenient는 {top_lenient}({drops[top_lenient][1]}쌍)이다.", "",
              f"![ablation](../{figs['ablation']})", "",
              f"lenient로도 놓친 {overall['eval'] - overall['lenient']}쌍의 원인: "
              + "; ".join(f"{cause} {n}쌍" for cause, n in causes.most_common()) + ".", ""]
    return lines


def stratified_table(live_summaries) -> list[str]:
    rows = []
    for name, summary in stratified_runs(live_summaries):
        significance = load_significance(name)
        if not significance:
            continue
        for c, metrics in significance["conditions"].items():
            failures = metrics["gate_failures"]["benign"]
            rows.append([summary["agent_model"], c]
                        + [cell for key, _ in LIVE_METRICS for cell in (format_ci(metrics[key]),
                                                                       format_paired_difference(metrics[key]))]
                        + ["-" if c == "C0" else f"{failures['n']} ({failures['c0_succeeded']})"])
    if not rows:
        return []
    header = ["모델", "조건"] + [h for _, label in LIVE_METRICS for h in (f"{label} [95% CI]", "C0 대비")]
    return [markdown_table(header + ["게이트로 실패한 정상 실행 (C0 성공)"], rows)]


def attack_class_table(live_summaries) -> list[str]:
    columns = [(summary["agent_model"], c, summary["conditions"][c]) for _, summary in stratified_runs(live_summaries)
               for c in summary["conditions"] if c != "C0"]
    classes = sorted({k for _, _, metrics in columns for k in metrics["successful_attacks_by_class"]})
    if not classes:
        return []
    return [markdown_table(["남은 공격 성공의 경로"] + [f"{model} {c}" for model, c, _ in columns],
                           [[cls] + [metrics["successful_attacks_by_class"].get(cls, 0) for _, _, metrics in columns]
                            for cls in classes])]


def rq2_section(live_summaries, figs) -> list[str]:
    # v1 live runs use the v1 extraction; the "-v3" directory belongs to section 6
    spec_models = ", ".join(p.name for p in sorted((PROJECT_ROOT / "data/specs/llm").iterdir())
                            if p.is_dir() and not p.name.endswith("-v3"))
    judge_models = ", ".join(p.name for p in sorted((PROJECT_ROOT / "data/judge_cache").iterdir()) if p.is_dir())
    lines = ["## 3. RQ2 실제 에이전트", "",
             "AgentDojo `AgentPipeline`에 게이트(`GatedRuntime`)와 `TaskSpecElement`를 넣어 실행했다. 게이트 조건의 TaskSpec은 "
             f"LLM spec(`data/specs/llm/{spec_models}/`), C4 판정 LLM은 `{judge_models}`이다.", "",
             "비율 옆 괄호는 Wilson 95% CI, \"C0 대비\"는 같은 (suite, user task, injection task) 짝의 차이와 exact McNemar p다. "
             "마지막 열은 차단이 있었고 user task가 실패한 정상 실행 수(그중 C0에서는 성공한 수)다.", ""]
    lines += stratified_table(live_summaries) + [""]
    if "strat_ci" in figs:
        lines += [f"![stratified sample CI](../{figs['strat_ci']})", ""]
    noise = [(summary["agent_model"], load_significance(name)["benign_unblocked_condition_pairs"])
             for name, summary in stratified_runs(live_summaries) if load_significance(name)]
    if noise:
        lines += ["차단이 없었던 정상 실행끼리 조건만 바꿔 비교하면 Utility가 달라진 짝은 "
                  + ", ".join(f"{model} {n['utility_differs']}/{n['pairs']}({format_rate(n['rate'])})"
                              for model, n in noise)
                  + "이다. 게이트가 개입하지 않은 차이이므로 모델 무작위성의 크기를 보여 준다.", ""]
    lines += [" ".join(f"![tradeoff {summary['agent_model']}](../{figs['tradeoff_' + name]})"
                       for name, summary in stratified_runs(live_summaries)), ""]
    table = attack_class_table(live_summaries)
    if table:
        lines += ["게이트 조건에서 남은 공격 성공을 경로별로 센 것이다(`summary.json`의 `successful_attacks_by_class`).", ""]
        lines += table + [""]
    return lines


def judge_section(judge) -> list[str]:
    if not judge:
        return []
    rows = []
    for model, judge_eval in judge.items():
        for label in ("attack", "normal"):
            answers, final = judge_eval["confusion_answer"], judge_eval["confusion_final"]
            rows.append([model, label, sum(v for k, v in final.items() if k.startswith(f"{label}->"))]
                        + [answers.get(f"{label}->{a}", 0) for a in "ABC"]
                        + [final.get(f"{label}->{f}", 0) for f in ("ALLOW", "BLOCK", "HUMAN")])
    return ["## 4. 판정 LLM 단독 평가", "",
            "재생(oracle spec)에서 ESCALATE가 난 호출을 판정기에 넣고 정답(injection 궤적의 호출 = attack, "
            "정상 재생의 호출 = normal)과 비교했다. 답 A는 허용, B는 차단, C는 사람 확인이다.", "",
            markdown_table(["판정 모델", "정답", "호출", "답 A", "답 B", "답 C", "최종 ALLOW", "최종 BLOCK", "최종 HUMAN"],
                           rows), ""]


def enforcement_section(enforcement_checks) -> list[str]:
    return ["## 5. 집행 무결성", "",
            "`scripts/analysis/check_enforcement.py`가 실행 기록에서 BLOCK된 호출의 실행, 차단 시 환경 변화, "
            "툴 결과와 게이트 판정의 1:1 불일치를 센다.", "",
            markdown_table(["실행 묶음", "실행", "게이트 판정", "차단 집행", "위반", "반복 상한으로 미실행 요청이 있는 실행"],
                           [[f"`{name}`", check["runs"], check["gate_events"], check["blocked_events"], check["violations"],
                             check.get("runs_with_unexecuted_requests_at_loop_limit", 0)]
                            for name, check in enforcement_checks.items()]), ""]


def limitations_section(replay_summary) -> list[str]:
    n_injection_tasks = len(load_json(PROJECT_ROOT / "data/benchmark/injection_tasks.json") or [])
    design = pairs_file_design()
    return ["## 7. 한계 (1–5절)", "",
            f"- 정책과 공격 유형 분류를 평가에 쓴 injection task {n_injection_tasks}개를 보고 설계했다. 재생 탐지율은 일반화 성능이 "
            "아니라 설계 대상 위협에 대한 상한에 가깝다.",
            "- 재생은 넘어간 에이전트가 참조 궤적대로 공격한다고 가정한다. 실제 모델은 다른 경로로 시도하거나 차단 뒤 작업을 포기한다.",
            f"- 실제 모델 본 실험은 층화 표본(공격 {design['attack']}쌍)이며 전체 {replay_summary['n_attack_pairs']}쌍이 아니다. "
            "조건당 1회 실행이라 정상 Utility의 수 %p 차이는 모델 무작위성과 구분되지 않는다.",
            "- 유형 H(툴 호출 없는 출력 조작)는 툴 호출 게이트의 범위 밖이다. 정책을 아는 적응형 공격은 평가하지 않았다.",
            "- C3(AgentDojo 내장 방어)은 실제 모델로 실행하지 않았다.", ""]


def reproduce_section(llm_replay_summaries, live_summaries) -> list[str]:
    design = pairs_file_design()
    lines = ["## 8. 재현 명령", "",
             f"Python {tomllib.loads((PROJECT_ROOT / 'pyproject.toml').read_text())['project']['requires-python']}, "
             f"`agentdojo=={version('agentdojo')}`, 벤치마크 `{BENCHMARK_VERSION}`, 정책 설정 `config/policies.yaml` "
             f"(version {load_policy_config()['version']}). "
             "LLM 단계에는 `ANTHROPIC_API_KEY`, Ollama 모델에는 `OLLAMA_API_KEY`가 필요하다.", "",
             "```bash",
             "python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'",
             "python -m pytest -q",
             "python scripts/evaluation/replay_evaluation.py --spec oracle --out results/replay/v1/oracle",
             "python scripts/analysis/summarize_replay.py"]
    for model in llm_replay_summaries:
        lines += ["python scripts/taskspec/extract_llm_specs.py && "
                  f"python scripts/taskspec/compare_specs.py --model {model}",
                  f"python scripts/evaluation/replay_evaluation.py --spec llm:{model} --out results/replay/v1/llm_{model}",
                  f"python scripts/analysis/summarize_replay.py --run results/replay/v1/llm_{model}"]
    lines += ["python scripts/evaluation/judge_evaluation.py",
              f"python scripts/evaluation/make_stratified_pairs.py --k {design['k']} --seed {design['seed']} --benign "
              f"--out {PAIRS_FILE}"]
    for name, summary in live_summaries.items():
        args = load_run_args(name)
        model = summary["agent_model"]
        env = (f"AGENTGATE_AGENT_MODEL={model}" if model.startswith("claude")
               else f"AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL={model}")
        lines.append(f"{env} python scripts/evaluation/live_evaluation.py --agent llm --spec llm "
                     f"--attack {summary.get('attack')} --pairs @{PAIRS_FILE} --conditions {' '.join(summary['conditions'])} "
                     f"--workers {args.get('workers')} --out results/live/v1/{name}")
    lines += [f"for run in {' '.join(live_summaries)}; do",
              "  python scripts/analysis/check_enforcement.py --run results/live/v1/$run",
              "  python scripts/analysis/summarize_live.py --run results/live/v1/$run",
              "  python scripts/analysis/live_significance.py --run results/live/v1/$run",
              "done"]
    lines += ["# 6절 (v2–v3.1)",
              "python scripts/evaluation/toolspec_evaluation.py --out results/toolspec/v1",
              "python scripts/analysis/analyze_atr_corpora.py --atr <agent-threat-rules checkout> --out results/rules/v1",
              "python scripts/evaluation/extract_taskspecs.py --name claude-sonnet-5-5-v3 --cache-dir data/specs/llm_cache_v3",
              "python scripts/evaluation/make_stratified_pairs.py --k 3 --seed 1 --benign --out data/pairs/strat3_seed1.txt",
              "for a in tool_knowledge important_instructions; do for e in judge_v3 judge_v3_norules; do",
              "  python scripts/evaluation/replay_evaluation.py --spec llm:claude-sonnet-5-5-v3 --gate v31 --escalate $e "
              "--attack $a --out results/replay/v31/${e}_llmspec_$a",
              "  python scripts/analysis/summarize_replay.py --run results/replay/v31/${e}_llmspec_$a; done; done",
              "AGENTGATE_AGENT_PROVIDER=ollama AGENTGATE_AGENT_MODEL=gpt-oss:120b python scripts/evaluation/live_evaluation.py "
              "--agent llm --spec llm --attack tool_knowledge --pairs @data/pairs/strat3_seed1.txt "
              "--conditions C0 C1 C6 C7 C7n --spec-cache-dir data/specs/llm_cache_v3 "
              "--out results/live/v31/strat3s1_tk_gpt-oss_120b",
              "python scripts/analysis/analyze_live_utility.py --run results/live/v31/strat3s1_tk_gpt-oss_120b "
              "--baseline results/live/v31/strat3s1_tk_gpt-oss_120b --condition C7"]
    lines += ["python scripts/reporting/build_report.py", "```"]
    return lines

# ---------------------------------------------------------------------------------------------- v2–v3.1 (section 6)
V3_REPLAY, V31_REPLAY = PROJECT_ROOT / "results/replay/v3", PROJECT_ROOT / "results/replay/v31"
V31_SEED0 = PROJECT_ROOT / "results/live/v31/strat3_tk_gpt-oss_120b"     # pairs used to analyse the v3 losses
V31_SEED1 = PROJECT_ROOT / "results/live/v31/strat3s1_tk_gpt-oss_120b"   # pairs not used for the v3.1 design
TYPE_H_CLASS = "(a) 툴 호출 없이 성공(유형 H)"
V3_CONDITION_NAMES = {
    "C0": "방어 없음", "C1": "v1 게이트, ESCALATE→BLOCK", "C2": "v1 게이트, ESCALATE→ALLOW",
    "C4": "v1 게이트 + 판정 LLM v1", "C5": "v2: P10(PIGuard 라벨) + Trusted LLM v2",
    "C6": "v3: P11·P12 + ToolSpec + 판정 LLM v3(규칙 문서 v3)", "C7": "v3.1 + 판정 LLM v3(규칙 문서 v3.1)",
    "C7n": "v3.1 + 판정 LLM v3(규칙 문서 없음)"}
REPLAY_V3_RUNS = [("v1 게이트 (P7 공격 문구 포함)", V3_REPLAY, "v1gate_llmspec"),
                  ("v3", V3_REPLAY, "judge_v3_llmspec"),
                  ("v3.1 + 규칙 문서", V31_REPLAY, "judge_v3_llmspec"),
                  ("v3.1, 규칙 문서 없음", V31_REPLAY, "judge_v3_norules_llmspec")]


def _pairs(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def _tool_call_successes(summary: dict, significance: dict, condition: str) -> tuple[int, int]:
    """(all attack successes, successes that needed no tool call = type H)."""
    k = significance["conditions"][condition]["asr"]["k"]
    return k, summary["conditions"][condition]["successful_attacks_by_class"].get(TYPE_H_CLASS, 0)


def _live_rows(summary: dict, significance: dict, conditions: list[str]) -> list[list]:
    rows = []
    for c in conditions:
        if c not in significance["conditions"]:
            continue
        metrics = significance["conditions"][c]
        total, type_h = _tool_call_successes(summary, significance, c)
        rows.append([c, V3_CONDITION_NAMES[c], format_ci(metrics["asr"]), total - type_h,
                     format_ci(metrics["utility"]), format_paired_difference(metrics["utility"]),
                     format_ci(metrics["utility_under_attack"])])
    return rows


LIVE_HEADER = ["조건", "구성", "ASR [95% CI]", "툴 호출 공격 성공", "Utility [95% CI]", "Utility C0 대비",
               "공격 중 Utility [95% CI]"]


V2_FOLDER, V3_FOLDER = PROJECT_ROOT / "v2_detector_trusted_llm", PROJECT_ROOT / "v3_trusted_llm_policy_doc"
REPLAY_V2 = PROJECT_ROOT / "results/replay/v2"
V2_LIVE = PROJECT_ROOT / "results/live/v2/strat3_tk_gpt-oss_120b"


def _live_inputs():
    return (load_json(V31_SEED0 / "comparison_all_summary.json"), load_json(V31_SEED0 / "comparison_all_significance.json"),
            load_json(V31_SEED1 / "summary.json"), load_json(V31_SEED1 / "significance.json"))


def _enforcement_rows(checks: list[tuple[str, Path]]) -> list[list]:
    return [[label, check["runs"], check["gate_events"], check["blocked_events"], check["violations"]]
            for label, path in checks if (check := load_json(path / "enforcement_check.json"))]


def v3_section() -> list[str]:
    """final_report 6절: 버전별 구조 요약과 같은 짝의 live 비교. 버전별 세부 결과는 각 폴더의 RESULTS.md."""
    seed0_summary, seed0_sig, seed1_summary, seed1_sig = _live_inputs()
    if not all((seed0_summary, seed0_sig, seed1_summary, seed1_sig)):
        return []
    s0, s1 = _pairs(PROJECT_ROOT / "data/pairs/strat3_seed0.txt"), _pairs(PROJECT_ROOT / "data/pairs/strat3_seed1.txt")
    attack0, attack1 = {x for x in s0 if not x.endswith(":none")}, {x for x in s1 if not x.endswith(":none")}
    return ["## 6. v2–v3.1 후속 실험", "",
            "1–5절의 v1 게이트 위에서 진행했다. 에이전트는 gpt-oss:120b(Ollama), 공격은 `tool_knowledge`, TaskSpec은 "
            "claude-sonnet-5-5가 프롬프트와 툴 목록만 보고 새로 추출한 것(`data/specs/llm/claude-sonnet-5-5-v3/`)이다. "
            "버전별 구조·코드·세부 결과는 두 폴더에 나눠 두었다.", "",
            markdown_table(["버전", "구성", "폴더"], [
                ["v2 (C5)", "툴 응답 판별기(PIGuard) 라벨 → P10, ESCALATE는 Trusted LLM v2. 판별기 오탐으로 v3에서 폐기",
                 "[`v2_detector_trusted_llm/`](../v2_detector_trusted_llm/README.md)"],
                ["v3·v3.1 (C6·C7·C7n)", "ToolSpec(툴 응답→JSON) + P11·P12, ESCALATE는 판정 LLM v3 + 규칙 문서",
                 "[`v3_trusted_llm_policy_doc/`](../v3_trusted_llm_policy_doc/README.md)"]]), "",
            "### 6.1 실제 에이전트 비교", "",
            "\"툴 호출 공격 성공\"은 공격 성공 중 툴 호출 없이 답변만 조작한 유형 H를 뺀 수다. \"C0 대비\"는 같은 짝의 차이와 "
            "exact McNemar p다.", "",
            "**짝 A (`data/pairs/strat3_seed0.txt`, v3 손실 분석과 v3.1 설계에 쓴 짝)**. C0–C4는 v1, C5는 v2, C6은 v3 "
            "실행 결과를 재사용했다(같은 짝, 다른 실행 시점).", "",
            markdown_table(LIVE_HEADER, _live_rows(seed0_summary, seed0_sig, ["C0", "C1", "C2", "C4", "C5", "C6", "C7", "C7n"])),
            "",
            f"**짝 B (`data/pairs/strat3_seed1.txt`, v3.1 설계에 쓰지 않은 짝)**. 다섯 조건을 같은 시점에 실행했다. "
            f"정상 실행은 짝 A와 같은 97개 user task이고, 공격 쌍 {len(attack1)}개 중 {len(attack0 & attack1)}개는 짝 A와 "
            "겹친다. 완전한 held-out은 아니다.", "",
            markdown_table(LIVE_HEADER, _live_rows(seed1_summary, seed1_sig, ["C0", "C1", "C6", "C7", "C7n"])), ""]


def v2_results() -> list[str]:
    c5 = load_json(V2_LIVE / "utility_analysis_C5.json")
    probe = load_json(PROJECT_ROOT / "results/detector/v1/format_probe.json")
    seed0_summary, seed0_sig, _, _ = _live_inputs()
    if not all((c5, probe, seed0_summary, seed0_sig)):
        return []
    detector = c5["detector_on_benign_runs"]
    lines = ["# v2 결과: 판별기 + Trusted LLM", "",
             "> `scripts/reporting/build_report.py`가 생성한다. 손으로 고치지 않는다. 구조는 [README](README.md).", "",
             "## 1. 판별기 오탐", "",
             f"live C5의 정상 실행에서 툴 응답 {detector['responses']}개 중 {percent_of(detector['flagged'], detector['responses'])}"
             "를 PIGuard가 flag했다(`results/live/v2/.../utility_analysis_C5.json`). 많이 flag된 툴:", "",
             markdown_table(["툴", "flag 수"], [[f"`{t}`", n] for t, n in
                                                list(detector["flagged_by_tool"].items())[:6]]), "",
             "같은 내용을 출력 형식만 바꿔 넣은 점수다(`results/detector/v1/format_probe.json`). 판별기는 내용보다 형식에 반응했다.",
             "", markdown_table(["입력", "PIGuard", "protectai-v2"],
                                [[f"`{name}`", s["piguard"], s["protectai-v2"]] for name, s in probe["scores"].items()]), ""]
    rows = []
    for label, name in (("v1 게이트 (회귀 기준)", "regression_v1_gate"), ("v2, ESCALATE→BLOCK", "c5_block_piguard"),
                        ("v2, ESCALATE→Trusted LLM v2", "c5_judge_piguard")):
        summary = load_json(REPLAY_V2 / name / "summary.json")
        if summary:
            prevention = summary["prevention"]
            rows.append([label, percent_of(prevention["prevented"], prevention["baseline_success"]),
                         percent_of(summary["benign"]["utility_enforce"], summary["n_benign"]),
                         percent_of(summary["utility_under_attack"]["enforce"], summary["utility_under_attack"]["n"])])
    lines += ["## 2. 재생 (oracle TaskSpec, `important_instructions`)", "",
              markdown_table(["게이트", "저지", "정상 Utility", "공격 중 Utility"], rows), "",
              "## 3. 실제 에이전트 (짝 A)", "",
              markdown_table(LIVE_HEADER, _live_rows(seed0_summary, seed0_sig, ["C0", "C1", "C5"])), "",
              "## 4. 집행 무결성", "",
              markdown_table(["실행 묶음", "실행", "게이트 판정", "차단 집행", "위반"], _enforcement_rows([("v2 C5", V2_LIVE)])), "",
              "## 5. 결론", "",
              "- 판별기 라벨이 정책의 ALLOW를 덮어써 판단 주체가 정책에서 판별기로 넘어갔고, 정상 작업 손실의 주원인이 되었다.",
              "- v3부터 판별기를 쓰지 않는다. 툴 응답은 ToolSpec(JSON)으로 바꿔 정책이 판단한다.", ""]
    return lines


def v3_results() -> list[str]:
    seed0_summary, seed0_sig, seed1_summary, seed1_sig = _live_inputs()
    toolspec = load_json(PROJECT_ROOT / "results/toolspec/v1/summary.json")
    atr = load_json(PROJECT_ROOT / "results/rules/v1/atr_taxonomy.json")
    if not all((seed0_summary, seed0_sig, seed1_summary, seed1_sig, toolspec, atr)):
        return []
    rules = yaml.safe_load((PROJECT_ROOT / "config/policy_rules_v31.yaml").read_text())["rules"]
    indirect = atr["by_kind"]["indirect"]
    techniques = indirect["techniques"]
    lines = ["# v3·v3.1 결과: Trusted LLM + 정책 문서", "",
             "> `scripts/reporting/build_report.py`가 생성한다. 손으로 고치지 않는다. 구조는 [README](README.md), "
             "다른 조건과의 live 비교는 [`docs/final_report.md`](../docs/final_report.md) 6절.", "",
             "## 1. 정책 문서의 근거", "",
             f"`config/policy_rules_v31.yaml`: 코드 집행 조항 {sum(r['layer'] == 'code' for r in rules)}개, 판정 LLM 조항 "
             f"{sum(r['layer'] == 'judge' for r in rules)}개와 조항별 트리거 인덱스. 근거는 ATR test-corpora "
             f"(commit `{atr['atr_commit'][:12]}`) 사례 {atr['samples'] - atr['errors']}건(분류 실패 {atr['errors']}건 제외)을 "
             f"claude-haiku-5-5로 분류한 결과다. 간접 주입 {indirect['n']}건 중 평범한 요청 형태 "
             f"{techniques.get('ordinary_request_in_data', 0)}건, 권한 사칭 {techniques.get('authority_impersonation', 0)}건, "
             f"이전 지시 무시 {techniques.get('instruction_override', 0)}건, AI를 지칭함 {indirect['addressed_to_ai']}건이다. "
             "AgentDojo 공격 문구는 쓰지 않았다. 다만 코드 조항은 AgentDojo 툴 환경에서 설계한 P1–P9를 일반화한 것이고, "
             "v3.1 수정은 AgentDojo live의 정상 작업 손실을 보고 만들었다.", ""]
    ts = toolspec["all"]
    lines += ["## 2. ToolSpec 추출 품질", "",
              "정답 궤적의 실제 툴 응답(정상, 그리고 공격 2종으로 주입된 응답)에서 '독자에게 하는 요청'을 뽑은 결과다"
              "(`results/toolspec/v1`).", "",
              markdown_table(["응답", "개수", "assistant 대상 요청 추출", "요청 하나라도 추출"],
                             [["주입 응답", ts["injected_responses"],
                               percent_of(ts["injected_detected_strict"]["n"], ts["injected_detected_strict"]["of"]),
                               percent_of(ts["injected_detected_any"]["n"], ts["injected_detected_any"]["of"])],
                              ["정상 응답", ts["benign_responses"],
                               percent_of(ts["benign_flagged_strict"]["n"], ts["benign_flagged_strict"]["of"]),
                               percent_of(ts["benign_flagged_any"]["n"], ts["benign_flagged_any"]["of"])]]), "",
              f"AgentDojo의 주입은 AI를 직접 부르는 문장이다. ATR 간접 주입에서는 {indirect['n'] - indirect['addressed_to_ai']}/"
              f"{indirect['n']}건이 AI를 지칭하지 않으므로, 이 탐지율을 다른 공격에 그대로 기대할 수는 없다.", ""]
    rows = []
    for label, root, prefix in REPLAY_V3_RUNS:
        for attack in ("tool_knowledge", "important_instructions"):
            summary = load_json(root / f"{prefix}_{attack}" / "summary.json")
            if not summary:
                continue
            prevention, overall = summary["prevention"], summary["overall"]
            rows.append([label, f"`{attack}`", percent_of(prevention["prevented"], prevention["baseline_success"]),
                         percent_of(overall["lenient"], overall["eval"]),
                         percent_of(summary["benign"]["utility_enforce"], summary["n_benign"]),
                         percent_of(summary["utility_under_attack"]["enforce"], summary["utility_under_attack"]["n"]),
                         sum(len(v) for v in summary["misses"].values())])
    lines += ["## 3. 재생 (LLM TaskSpec v3)", "",
              "정답 궤적 재생이다. 네 게이트 모두 같은 TaskSpec을 쓴다. \"놓친 쌍\"은 효과 호출이 BLOCK도 ESCALATE도 "
              "되지 않은 공격 쌍이다.", "",
              markdown_table(["게이트", "공격", "저지", "lenient 탐지", "정상 Utility", "공격 중 Utility", "놓친 쌍"], rows), ""]
    lines += ["## 4. 실제 에이전트", "",
              markdown_table(LIVE_HEADER, _live_rows(seed1_summary, seed1_sig, ["C0", "C1", "C6", "C7", "C7n"])), "",
              "짝 B(`data/pairs/strat3_seed1.txt`, v3.1 설계에 쓰지 않은 짝)의 같은 시점 실행이다. 짝 A 결과는 "
              "`docs/final_report.md` 6.1절에 있다.", ""]
    loss_rows = []
    for label, path in (("짝 A C6 (v3)", PROJECT_ROOT / "results/live/v3/strat3_tk_gpt-oss_120b/utility_analysis_C6.json"),
                        ("짝 A C7 (v3.1)", V31_SEED0 / "utility_analysis_C7.json"),
                        ("짝 B C6 (v3)", V31_SEED1 / "utility_analysis_C6.json"),
                        ("짝 B C7 (v3.1)", V31_SEED1 / "utility_analysis_C7.json")):
        analysis = load_json(path)
        if analysis:
            pairing = analysis["benign"]["pairing"]
            loss_rows.append([label, pairing.get("C0=ok cond=fail blocked", 0), pairing.get("C0=ok cond=fail no_block", 0),
                              pairing.get("C0=fail cond=ok no_block", 0)])
    if loss_rows:
        lines += ["정상 실행 97개를 같은 짝의 C0와 비교한 것이다(`scripts/analysis/analyze_live_utility.py`). 차단 없이 갈린 두 열은 "
                  "에이전트 실행마다의 편차다.", "",
                  markdown_table(["조건", "C0 성공 → 차단으로 실패", "C0 성공 → 차단 없이 실패", "C0 실패 → 성공"], loss_rows), ""]
    rule_rows = []
    for label, summary, significance in (("짝 A", seed0_summary, seed0_sig), ("짝 B", seed1_summary, seed1_sig)):
        for c in ("C7", "C7n"):
            total, type_h = _tool_call_successes(summary, significance, c)
            metrics, cond = significance["conditions"][c], summary["conditions"][c]
            rule_rows.append([label, f"{c} ({'있음' if c == 'C7' else '없음'})", total - type_h,
                              short_rate(metrics["utility"]), short_rate(metrics["utility_under_attack"]),
                              cond["judge_calls"], cond["human_count"], cond["judge_input_tokens_total"]])
    replay_rule_rows = []
    for c, prefix in (("있음", "judge_v3_llmspec"), ("없음", "judge_v3_norules_llmspec")):
        for attack in ("tool_knowledge", "important_instructions"):
            summary = load_json(V31_REPLAY / f"{prefix}_{attack}" / "summary.json")
            if summary:
                prevention = summary["prevention"]
                replay_rule_rows.append([c, f"`{attack}`", percent_of(prevention["prevented"], prevention["baseline_success"]),
                                         percent_of(summary["benign"]["utility_enforce"], summary["n_benign"]),
                                         percent_of(summary["utility_under_attack"]["enforce"],
                                                    summary["utility_under_attack"]["n"])])
    lines += ["## 5. 정책 문서의 효과 (C7 vs C7n)", "",
              "두 조건은 게이트(v3.1)와 판정 LLM에 넘기는 사실(TaskSpec, ToolSpec, 값의 출처)이 같고, 판정 LLM에 규칙 인덱스와 "
              "조항을 주는지만 다르다. 규칙 문서가 없으면 system 프롬프트가 짧아 프롬프트 캐시가 동작하지 않는다.", "",
              markdown_table(["짝", "규칙 문서", "툴 호출 공격 성공", "Utility", "공격 중 Utility", "판정 호출", "HUMAN",
                              "판정 입력 토큰(캐시 제외)"], rule_rows), "",
              markdown_table(["규칙 문서", "재생 공격", "저지", "정상 Utility", "공격 중 Utility"], replay_rule_rows), ""]
    lines += ["## 6. 집행 무결성과 한계", "",
              markdown_table(["실행 묶음", "실행", "게이트 판정", "차단 집행", "위반"], _enforcement_rows(
                  [("v3 C6", PROJECT_ROOT / "results/live/v3/strat3_tk_gpt-oss_120b"),
                   ("v3.1 짝 A", V31_SEED0), ("v3.1 짝 B", V31_SEED1)])), "",
              "- v3.1의 수정은 짝 A의 C6 손실을 보고 만들었다. 짝 B는 공격 쌍 일부만 새롭고 정상 user task는 같다.",
              "- v3.1 B1은 처음에 지목 자료 안 링크를 판정 없이 P7에 넘기도록 구현했다가, 파일럿에서 주입된 URL 방문이 통과해 "
              "ESCALATE로 바꿨다. 본 실행은 바꾼 뒤의 결과다.",
              "- ToolSpec 추출기는 툴 응답을 읽으므로 그 자체가 주입 대상이다. 자유 텍스트 필드를 두지 않고 값은 원문 대조로 검증하지만, "
              "추출기를 겨냥한 적응형 공격은 평가하지 않았다.",
              "- AgentDojo 밖의 벤치마크로 일반화를 검증하지 않았다.",
              "- 유형 H(툴 호출 없는 출력 조작)는 모든 조건에서 남는다. 조건당 1회 실행이다.", ""]
    return lines


def write_version_results():
    for folder, lines in ((V2_FOLDER, v2_results()), (V3_FOLDER, v3_results())):
        if lines:
            (folder / "RESULTS.md").write_text("\n".join(lines) + "\n")


def v3_summary_bullets() -> list[str]:
    seed1_summary, seed1_sig = load_json(V31_SEED1 / "summary.json"), load_json(V31_SEED1 / "significance.json")
    seed0_summary = load_json(V31_SEED0 / "comparison_all_summary.json")
    seed0_sig = load_json(V31_SEED0 / "comparison_all_significance.json")
    if not all((seed1_summary, seed1_sig, seed0_summary, seed0_sig)):
        return []
    conditions = seed1_sig["conditions"]

    def tool_attacks(summary, significance, c):
        total, type_h = _tool_call_successes(summary, significance, c)
        return total - type_h

    return [f"- **v3.1 (6절, 짝 B 같은 시점 실행)**: Utility C0 {short_rate(conditions['C0']['utility'])} → C7 "
            f"{short_rate(conditions['C7']['utility'])} (C0 대비 {format_paired_difference(conditions['C7']['utility'])}), "
            f"ASR {short_rate(conditions['C0']['asr'])} → {short_rate(conditions['C7']['asr'])}이며 남은 공격 성공은 모두 툴 호출 "
            f"없는 유형 H다(툴 호출 공격 {tool_attacks(seed1_summary, seed1_sig, 'C7')}건).",
            f"- **규칙 문서 ablation**: 판정 LLM에 규칙 문서를 주지 않으면(C7n) 툴 호출 공격 성공이 짝 A "
            f"{tool_attacks(seed0_summary, seed0_sig, 'C7')}→{tool_attacks(seed0_summary, seed0_sig, 'C7n')}건, 짝 B "
            f"{tool_attacks(seed1_summary, seed1_sig, 'C7')}→{tool_attacks(seed1_summary, seed1_sig, 'C7n')}건이 된다."]



def write_final_report(replay_summary, llm_replay_summaries, live_summaries, enforcement_checks, figs):
    judge_dir = PROJECT_ROOT / "results/judge/v1"
    judge = {path.parent.name: load_json(path) for path in sorted(judge_dir.glob("*/judge_eval.json"))}
    lines = ["# AgentDojo 툴 호출 정책 게이트 — 결과 보고서", "",
             "> 모든 수치는 `scripts/reporting/build_report.py`가 `results/`와 `data/`의 파일에서 생성한다. "
             "게이트 구조와 설계 결정은 [`docs/architecture.md`](architecture.md), "
             "정책 P1~P9의 정의는 [`docs/policy_spec.md`](policy_spec.md)에 있다. v2·v3의 세부 결과는 각 폴더의 RESULTS.md에 있다.", "",
             "## 요약", ""]
    lines += summary_bullets(replay_summary, llm_replay_summaries, live_summaries, enforcement_checks, judge)
    lines += v3_summary_bullets() + [""]
    lines += setup_section(replay_summary, live_summaries)
    lines += rq1_section(replay_summary, llm_replay_summaries, figs)
    lines += rq2_section(live_summaries, figs)
    lines += judge_section(judge)
    lines += enforcement_section(enforcement_checks)
    lines += v3_section()
    lines += limitations_section(replay_summary)
    lines += reproduce_section(llm_replay_summaries, live_summaries)
    (DOCS_DIR / "final_report.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------- dashboard
def write_dashboard(replay_summary, live_summaries):
    """Self-contained HTML: summary tables + per-pair trace viewer (replay audit/enforce)."""
    rows = []
    enforce_by_pair = {}
    for line in (REPLAY_DIR / "attack_enforce.jsonl").open():
        record = json.loads(line)
        enforce_by_pair[(record["suite"], record["user_task"], record["injection_task"])] = record
    labels = yaml.safe_load((PROJECT_ROOT / "data/benchmark/injection_labels.yaml").read_text())
    for line in (REPLAY_DIR / "attack_audit.jsonl").open():
        record = json.loads(line)
        pair_key = (record["suite"], record["user_task"], record["injection_task"])
        effects = [e for e in record["trace"] if e.get("role") == "effect"]
        rows.append({
            "s": record["suite"], "u": record["user_task"].replace("user_task_", ""), "i": record["injection_task"].replace("injection_task_", ""),
            "t": labels[record["suite"]][record["injection_task"]]["type"],
            "det": "BLOCK" if any(e["verdict"] == "BLOCK" for e in effects) else "ESCALATE" if any(e["verdict"] == "ESCALATE" for e in effects) else ("-" if not effects else "MISS"),
            "base": record["security"], "enf": enforce_by_pair[pair_key]["security"],
            "tr": [[e["phase"], e["tool"], json.dumps(e["args"], ensure_ascii=False)[:220], e["verdict"],
                    [[d["policy_id"], d["verdict"], d["reason"], d["evidence"].get("origin")] for d in e["decisions"]]] for e in record["trace"]],
        })
    for line in (REPLAY_DIR / "benign_audit.jsonl").open():
        record = json.loads(line)
        rows.append({"s": record["suite"], "u": record["user_task"].replace("user_task_", ""), "i": "", "t": "benign",
                     "det": max((e["verdict"] for e in record["trace"]), key=["ALLOW", "ESCALATE", "BLOCK"].index, default="ALLOW"),
                     "base": None, "enf": None,
                     "tr": [[e["phase"], e["tool"], json.dumps(e["args"], ensure_ascii=False)[:220], e["verdict"],
                             [[d["policy_id"], d["verdict"], d["reason"], d["evidence"].get("origin")] for d in e["decisions"]]] for e in record["trace"]]})
    overall, benign, prevention = replay_summary["overall"], replay_summary["benign"], replay_summary["prevention"]
    tiles = [("Lenient detection", percent_of(overall["lenient"], overall["eval"])), ("Strict detection", percent_of(overall["strict"], overall["eval"])),
             ("Prevented (enforce)", percent_of(prevention["prevented"], prevention["baseline_success"])), ("Benign tasks with BLOCK", percent_of(benign["tasks_block"], benign["tasks"]))]
    live_html = ""
    for name, summary in live_summaries.items():
        live_html += f"<h2>Pipeline run — agent: {name}</h2><table><tr><th>Condition</th><th>Utility</th><th>Utility under attack</th><th>ASR</th><th>Gate ms / task</th></tr>"
        for condition, metrics in summary["conditions"].items():
            live_html += f"<tr><td>{condition} {CONDITION_NAMES[condition]}</td><td>{format_rate(metrics['utility'])}</td><td>{format_rate(metrics['utility_under_attack'])}</td><td>{format_rate(metrics['asr'])}</td><td>{metrics['gate_ms_per_task']}</td></tr>"
        live_html += "</table>"
    html = DASHBOARD_TEMPLATE.replace("__VERSION__", BENCHMARK_VERSION).replace("__TILES__", "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in tiles)) \
               .replace("__LIVE__", live_html).replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
    (PROJECT_ROOT / "results" / "dashboard.html").write_text(html)


DASHBOARD_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Policy Gate Results</title><style>
:root{--bg:#fcfcfb;--fg:#0b0b0b;--mut:#52514e;--line:#e4e3df;--card:#fff;--block:#e34948;--esc:#c98500;--ok:#1baf7a;--miss:#4a3aa7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1a1a19;--fg:#fff;--mut:#c3c2b7;--line:#3a3a37;--card:#222220;--block:#e66767;--esc:#eda100;--ok:#199e70;--miss:#9085e9}}
:root[data-theme=dark]{--bg:#1a1a19;--fg:#fff;--mut:#c3c2b7;--line:#3a3a37;--card:#222220;--block:#e66767;--esc:#eda100;--ok:#199e70;--miss:#9085e9}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.k{color:var(--mut);font-size:12px}.v{font-size:18px;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.wrap{overflow-x:auto}.f{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}
.v-BLOCK{color:var(--block);font-weight:600}.v-ESCALATE{color:var(--esc);font-weight:600}.v-MISS{color:var(--miss);font-weight:600}.v-ALLOW{color:var(--mut)}
tr.pair{cursor:pointer}tr.pair:hover{background:var(--card)}.tr td{font-family:ui-monospace,monospace;font-size:12px}.dec{color:var(--mut)}
</style></head><body><main><h1>Policy gate (P1–P9) on AgentDojo __VERSION__</h1>
<p style="color:var(--mut)">Replay of ground-truth trajectories with oracle TaskSpecs. Click a row to see every tool call and the policy decisions behind it. Generated by scripts/reporting/build_report.py.</p>
<div class="tiles">__TILES__</div>__LIVE__
<h2>Pairs and benign tasks</h2><div class="f"><select id="s"><option value="">all suites</option><option>workspace</option><option>travel</option><option>banking</option><option>slack</option></select>
<select id="d"><option value="">all outcomes</option><option>BLOCK</option><option>ESCALATE</option><option>MISS</option><option>ALLOW</option><option>-</option></select>
<select id="t"><option value="">all types</option><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option><option>H</option><option>benign</option></select>
<input id="q" placeholder="user / injection id"><span id="n" style="color:var(--mut)"></span></div>
<div class="wrap"><table id="tab"><thead><tr><th>suite</th><th>user</th><th>injection</th><th>type</th><th>gate on effect calls</th><th>attack succeeds w/o gate</th><th>with gate (enforce)</th></tr></thead><tbody></tbody></table></div>
</main><script>
const D=__DATA__;const tb=document.querySelector('#tab tbody');const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function render(){const s=v('s'),d=v('d'),t=v('t'),q=v('q');let n=0;tb.innerHTML='';
for(const r of D){if(s&&r.s!==s||d&&r.det!==d||t&&r.t!==t||q&&!(r.u===q||r.i===q))continue;if(++n>400)continue;
const tr=document.createElement('tr');tr.className='pair';tr.innerHTML=`<td>${r.s}</td><td>${r.u}</td><td>${r.i}</td><td>${r.t}</td><td class="v-${r.det}">${r.det}</td><td>${r.base===null?'':r.base?'yes':'no'}</td><td>${r.enf===null?'':r.enf?'yes':'no'}</td>`;
tr.onclick=()=>{const nx=tr.nextSibling;if(nx&&nx.classList&&nx.classList.contains('tr')){nx.remove();return}const x=document.createElement('tr');x.className='tr';
x.innerHTML='<td colspan=7>'+r.tr.map(e=>`<div><b>[${e[0]}]</b> ${esc(e[1])}(${esc(e[2])}) → <span class="v-${e[3]}">${e[3]}</span>`+e[4].map(d=>`<div class="dec">&nbsp;&nbsp;${d[0]} <span class="v-${d[1]}">${d[1]}</span> ${esc(d[2])}${d[3]?' ['+d[3]+']':''}</div>`).join('')+'</div>').join('')+'</td>';tr.after(x)};tb.appendChild(tr)}
document.querySelector('#n').textContent=n+' rows'+(n>400?' (first 400 shown)':'')}
function v(id){return document.querySelector('#'+id).value.trim()}['s','d','t','q'].forEach(i=>document.querySelector('#'+i).addEventListener('input',render));render();
</script></body></html>"""



def main():
    replay_summary = load_json(REPLAY_DIR / "summary.json")
    llm_replay_summaries = {path.parent.name.removeprefix("llm_"): load_json(path)
                            for path in sorted((PROJECT_ROOT / "results/replay/v1").glob("llm_*/summary.json"))}
    live_summaries = {path.parent.name: load_json(path) for path in sorted(LIVE_ROOT.glob("strat3_*/summary.json"))}
    enforcement_checks = {name: load_json(LIVE_ROOT / name / "enforcement_check.json") for name in live_summaries
                          if (LIVE_ROOT / name / "enforcement_check.json").exists()}
    figs = build_figures(replay_summary, live_summaries)
    write_final_report(replay_summary, llm_replay_summaries, live_summaries, enforcement_checks, figs)
    write_version_results()
    write_dashboard(replay_summary, live_summaries)
    print("wrote docs/final_report.md", *sorted(f for f in figs.values()), "results/dashboard.html",
          "v2_detector_trusted_llm/RESULTS.md", "v3_trusted_llm_policy_doc/RESULTS.md")


if __name__ == "__main__":
    main()
