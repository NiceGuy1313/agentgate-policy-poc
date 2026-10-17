"""v3 결과 요약을 results/v3/RESULTS.md로 만든다. 수치는 모두 실행 기록에서 계산한다(손으로 고치지 않는다).

세 비교: 판정 LLM 없음(C8b) / 판정 LLM + 정책 문서(C8) / 판정 LLM, 정책 문서 없음(C8n). 기준선 C0는 같은 실행.
선행: check_enforcement.py, summarize_live.py, live_significance.py를 같은 --run에 실행
출력: results/v3/RESULTS.md

사용법: python scripts/v3/summarize_v3.py --run results/v3/live/full_tk_gpt-oss_120b
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from agentgate.agentdojo.live_run_analysis import live_run_files
from agentgate.agentdojo.metrics import mcnemar_exact
from agentgate.conditions import get_condition
from agentgate.config import PROJECT_ROOT, load_dotenv
from agentgate.v3.toolspec import DEFAULT_MODEL as TOOLSPEC_DEFAULT_MODEL

V3_DIR = PROJECT_ROOT / "results" / "v3"
METRICS = {"ASR": ("attack", "security"), "Utility": ("benign", "utility"),
           "공격 중 Utility": ("attack", "utility")}
COMPARISONS = [("C8", "C8b", "판정 LLM 효과(정책 문서 있음)"), ("C8n", "C8b", "판정 LLM 효과(정책 문서 없음)"),
               ("C8", "C8n", "정책 문서 효과")]


def table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + \
           ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]


def metric_cell(m: dict) -> str:
    lo, hi = m["ci95"]
    return f"{100 * m['rate']:.1f}% ({m['k']}/{m['n']}) [{100 * lo:.1f}, {100 * hi:.1f}]"


def paired(runs: list[dict], a: str, b: str, kind: str, field: str) -> dict:
    """Same (suite, user task, injection task) in conditions a and b: rate difference a−b and exact McNemar p."""
    def key(r):
        return r["suite"], r["user_task"], r["injection_task"]
    def keep(r):
        return not r.get("error") and (r["injection_task"] is None) == (kind == "benign")
    by_a = {key(r): bool(r[field]) for r in runs if r["condition"] == a and keep(r)}
    by_b = {key(r): bool(r[field]) for r in runs if r["condition"] == b and keep(r)}
    keys = by_a.keys() & by_b.keys()
    a_only = sum(by_a[k] and not by_b[k] for k in keys)
    b_only = sum(by_b[k] and not by_a[k] for k in keys)
    return {"n_pairs": len(keys), "a_only": a_only, "b_only": b_only,
            "diff": (a_only - b_only) / len(keys) if keys else None, "p_mcnemar": mcnemar_exact(b_only, a_only)}


def analyse(runs: list[dict], conditions: list[str]) -> dict:
    out: dict = {"conditions": {}, "comparisons": {}}
    for c in conditions:
        cruns = [r for r in runs if r["condition"] == c]
        events = [e for r in cruns for e in r["trace"]]
        judges = [(e["verdict"], e["judge"]) for e in events if e.get("judge")]
        toolspec = Counter()
        for r in cruns:
            for k, v in (r.get("toolspec") or {}).items():
                if isinstance(v, (int, float)):
                    toolspec[k] += v
        benign_lost = Counter()
        for r in cruns:
            if r["injection_task"] is not None or r["utility"] or r.get("error"):
                continue
            blocks = [e for e in r["trace"] if e.get("effective") == "BLOCK"]
            benign_lost["판정 LLM 차단" if blocks and blocks[0].get("judge") else
                        "엔진 차단" if blocks else "차단 없이 실패"] += 1
        out["conditions"][c] = {
            "runs": len(cruns), "errors": sum(bool(r.get("error")) for r in cruns),
            "judge_calls": len(judges),
            "engine_to_final": dict(Counter(f"{v}→{j['final']}" for v, j in judges)),
            "judge_api_calls": sum(1 for _, j in judges if j.get("answer") and not j.get("cached")),
            "judge_input_tokens": sum(j.get("input_tokens", 0) for _, j in judges),
            "judge_cache_read_tokens": sum(j.get("cache_read_input_tokens", 0) for _, j in judges),
            "judge_cache_write_tokens": sum(j.get("cache_creation_input_tokens", 0) for _, j in judges),
            "judge_output_tokens": sum(j.get("output_tokens", 0) for _, j in judges),
            "toolspec": dict(toolspec), "benign_lost": dict(benign_lost),
        }
    for a, b, _ in COMPARISONS:
        if a in conditions and b in conditions:
            out["comparisons"][f"{a}-{b}"] = {name: paired(runs, a, b, kind, field)
                                              for name, (kind, field) in METRICS.items()}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", default=str(V3_DIR / "RESULTS.md"))
    parser.add_argument("--toolspec", default=str(V3_DIR / "toolspec" / "summary.json"),
                        help="scripts/v3/toolspec_evaluation.py summary (section skipped if missing)")
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    summary = json.loads((run_dir / "summary.json").read_text())
    significance = json.loads((run_dir / "significance.json").read_text())
    enforcement = json.loads((run_dir / "enforcement_check.json").read_text())
    runs = [json.loads(p.read_text()) for p in live_run_files(run_dir)]
    conditions = list(summary["conditions"])
    analysis = analyse(runs, conditions)
    rel = run_dir.relative_to(PROJECT_ROOT) if run_dir.is_relative_to(PROJECT_ROOT) else run_dir
    first = summary["conditions"][conditions[0]]
    judge_model = next((j.get("model") for r in runs for e in r["trace"] if (j := e.get("judge")) and j.get("model")),
                       "-")
    spec_model = next((r["spec_meta"]["source"].removeprefix("llm:") for r in runs
                       if (r.get("spec_meta") or {}).get("source")), "-")
    load_dotenv()
    toolspec_model = os.environ.get("AGENTGATE_TOOLSPEC_MODEL") or TOOLSPEC_DEFAULT_MODEL

    lines = ["# v3 결과: 판정 LLM이 모든 effect 호출을 판단", "",
             f"> `scripts/v3/summarize_v3.py --run {rel}`가 생성한다. 손으로 고치지 않는다. 실행 기록(json)은 요약 후 지웠다.", "",
             "## 구성", "",
             f"- 에이전트 `{summary['agent_model']}`, 공격 `{summary['attack']}`, AgentDojo v1.2.2, 조건마다 공격 없음 "
             f"{first['benign_runs']}건 + 공격 {first['attack_runs']}건.",
             f"- TaskSpec `{spec_model}`, ToolSpec `{toolspec_model}`, "
             f"판정 LLM `{judge_model}`. 정책: `config/v3/policy.yaml`(gate v31).", ""]
    lines += table(["조건", "구성"], [[c, get_condition(c).description] for c in conditions]) + [""]

    def rate(c, name):
        m = significance["conditions"][c][name]
        return f"{100 * m['rate']:.1f}%"

    def diff(a, b, name):
        comp = analysis["comparisons"].get(f"{a}-{b}", {}).get(name)
        if not comp or comp["diff"] is None:
            return "-"
        sig = "유의" if comp["p_mcnemar"] < 0.05 else "유의하지 않음"
        return f"{100 * comp['diff']:+.1f}%p (p={comp['p_mcnemar']:.3g}, {sig})"
    if {"C0", "C8", "C8n", "C8b"} <= set(conditions):
        lines += ["## 핵심", "",
                  f"- 세 방식 모두 ASR을 C0 {rate('C0', 'asr')}에서 C8b {rate('C8b', 'asr')}, C8 {rate('C8', 'asr')}, "
                  f"C8n {rate('C8n', 'asr')}로 낮췄다.",
                  f"- 판정 LLM(C8 − C8b): 공격 중 Utility {diff('C8', 'C8b', '공격 중 Utility')}, "
                  f"ASR {diff('C8', 'C8b', 'ASR')}, 정상 Utility {diff('C8', 'C8b', 'Utility')}.",
                  f"- 정책 문서(C8 − C8n): ASR {diff('C8', 'C8n', 'ASR')}, 공격 중 Utility "
                  f"{diff('C8', 'C8n', '공격 중 Utility')}, 정상 Utility {diff('C8', 'C8n', 'Utility')}.",
                  f"- 정상 Utility는 C0 {rate('C0', 'utility')} 대비 C8 {rate('C8', 'utility')}, "
                  f"C8n {rate('C8n', 'utility')}, C8b {rate('C8b', 'utility')}.", ""]

    lines += ["## 결과", "", "Wilson 95% CI, \"C0 대비\"는 같은 짝의 차이와 exact McNemar p.", ""]
    rows = []
    for c in conditions:
        m = significance["conditions"][c]
        cells = [c]
        for name in ("asr", "utility", "utility_under_attack"):
            p = m[name].get("paired_vs_C0")
            cells += [metric_cell(m[name]), "-" if not p else f"{100 * p['diff']:+.1f}%p (p={p['p_mcnemar']:.3g})"]
        rows.append(cells)
    lines += table(["조건", "ASR", "C0 대비", "Utility", "C0 대비", "공격 중 Utility", "C0 대비"], rows) + [""]

    lines += ["## 세 비교", "", "같은 짝끼리의 차이(앞 조건 − 뒤 조건)와 exact McNemar p.", ""]
    rows = []
    for a, b, label in COMPARISONS:
        comp = analysis["comparisons"].get(f"{a}-{b}")
        if comp:
            rows.append([f"{label} ({a} − {b})"] + [
                f"{100 * comp[n]['diff']:+.1f}%p (p={comp[n]['p_mcnemar']:.3g})" if comp[n]["diff"] is not None
                else "-" for n in METRICS])
    lines += table(["비교", *METRICS], rows) + [""]

    lines += ["## 판정 LLM", "", "엔진 판정 → 판정 LLM 최종(HUMAN은 BLOCK으로 집행).", ""]
    rows = [[c, a["judge_calls"], ", ".join(f"{k} {v}" for k, v in sorted(a["engine_to_final"].items()))]
            for c, a in analysis["conditions"].items() if a["judge_calls"]]
    lines += table(["조건", "판정 호출", "엔진 → 최종"], rows) + [""]
    rows = [[c, f"{sum(a['benign_lost'].values())}/{first['benign_runs']}",
             ", ".join(f"{k} {v}" for k, v in sorted(a["benign_lost"].items(), key=lambda x: -x[1])) or "-"]
            for c, a in analysis["conditions"].items()]
    lines += ["공격 없는 실행의 실패 원인(첫 차단 기준):", ""] + table(["조건", "실패", "원인"], rows) + [""]
    rows = [[c, ", ".join(f"{k} {v}" for k, v in sorted(summary["conditions"][c]["successful_attacks_by_class"].items()))
             or "-"] for c in conditions if c != "C0"]
    lines += ["공격 성공 경로:", ""] + table(["조건", "경로"], rows) + [""]

    toolspec_path = Path(args.toolspec)
    if toolspec_path.exists():
        ts = json.loads(toolspec_path.read_text())
        a = ts["all"]

        def rate(m):
            return f"{100 * m['rate']:.1f}% ({m['n']}/{m['of']})"
        lines += ["## ToolSpec 추출 품질", "",
                  f"정답 궤적의 툴 응답(`{', '.join(ts['attacks'])}`, 중복 제거, `{ts['model']}`)에서 독자에게 하는 요청을 "
                  "뽑은 결과(`scripts/v3/toolspec_evaluation.py`).", ""]
        lines += table(["응답", "개수", "assistant 대상 요청 추출", "요청 하나라도 추출", "공격 목표 툴과 일치"], [
            ["주입 응답", a["injected_responses"], rate(a["injected_detected_strict"]), rate(a["injected_detected_any"]),
             rate(a["injected_goal_action_match"])],
            ["정상 응답", a["benign_responses"], rate(a["benign_flagged_strict"]), rate(a["benign_flagged_any"]), "-"]])
        lines += [""]

    lines += ["## 비용과 무결성", ""]
    rows = []
    for c, a in analysis["conditions"].items():
        t = a["toolspec"]
        rows.append([c, a["judge_api_calls"], a["judge_input_tokens"], a["judge_cache_read_tokens"],
                     a["judge_output_tokens"], int(t.get("api_calls", 0)), int(t.get("input_tokens", 0)),
                     int(t.get("cache_read_input_tokens", 0)), int(t.get("output_tokens", 0))])
    lines += table(["조건", "판정 API 호출", "판정 입력(캐시 외)", "판정 캐시 읽기", "판정 출력",
                    "ToolSpec API 호출", "ToolSpec 입력", "ToolSpec 캐시 읽기", "ToolSpec 출력"], rows) + [""]
    lines += [f"집행 무결성: 실행 {enforcement.get('runs', '-')}건, 차단 호출이 실행된 위반 "
              f"{enforcement.get('violations', '-')}건, 오류 {sum(a['errors'] for a in analysis['conditions'].values())}건.", ""]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
