"""v2 결과 요약을 results/v2/RESULTS.md로 만든다. 수치는 모두 실행 기록에서 계산한다(손으로 고치지 않는다).

선행: check_enforcement.py, summarize_live.py, live_significance.py를 같은 --run에 실행
입력: <run>/{C0,C5,C5p}/... 실행 기록, <run>/{summary,significance,enforcement_check,run_*}.json,
      results/v2/detector/tool_outputs_*.json(오프라인 판별기 평가), config/v2/detector.yaml
출력: results/v2/RESULTS.md, <run>/v2_analysis.json

사용법: python scripts/v2/summarize_v2.py --run results/v2/live/full_tk_gpt-oss_120b
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from agentgate.agentdojo.live_run_analysis import live_run_files
from agentgate.conditions import get_condition
from agentgate.config import PROJECT_ROOT
from agentgate.v2.detector import load_detector_config

V2_DIR = PROJECT_ROOT / "results" / "v2"


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}% ({k}/{n})" if n else "n/a"


def table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + \
           ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]


def metric_cell(m: dict) -> str:
    lo, hi = m["ci95"]
    return f"{100 * m['rate']:.1f}% ({m['k']}/{m['n']}) [{100 * lo:.1f}, {100 * hi:.1f}]"


def paired_cell(m: dict) -> str:
    p = m.get("paired_vs_C0")
    return "-" if not p else f"{100 * p['diff']:+.1f}%p (p={p['p_mcnemar']:.3g})"


def analyse(run_dir: Path) -> dict:
    runs = [json.loads(p.read_text()) for p in live_run_files(run_dir)]
    out: dict = {"conditions": {}}
    for condition in sorted({r["condition"] for r in runs}):
        cruns = [r for r in runs if r["condition"] == condition]
        events = [e for r in cruns for e in r["trace"]]
        judges = [e["judge"] for e in events if e.get("judge")]
        escalated = Counter(d["policy_id"] for e in events for d in e["decisions"] if d["verdict"] == "ESCALATE")
        blocked_by_policy = Counter(d["policy_id"] for e in events for d in e["decisions"] if d["verdict"] == "BLOCK")
        final_by_policy = Counter()
        for e in events:
            if e.get("judge"):
                ids = sorted({d["policy_id"] for d in e["decisions"] if d["verdict"] == "ESCALATE"})
                final_by_policy[(",".join(ids), e["judge"]["final"])] += 1
        benign = [r for r in cruns if r["injection_task"] is None]
        lost = Counter()
        for r in benign:
            if r["utility"]:
                continue
            blocks = [e for e in r["trace"] if e.get("effective") == "BLOCK"]
            if not blocks:
                lost["실패, 차단 없음"] += 1
                continue
            e = blocks[0]
            lost[f"판정 LLM {e['judge']['final']}" if e.get("judge") else
                 "정책 BLOCK " + ",".join(sorted({d['policy_id'] for d in e['decisions'] if d['verdict'] == 'BLOCK'}))] += 1
        detections = [(e["detection"], r["injection_task"] is not None) for r in cruns for e in r["trace"]
                      if e.get("detection")]
        det = Counter()
        for d, attack_run in detections:
            truth = "injected" if d.get("attack_marker") else ("clean_attack_run" if attack_run else "clean_benign_run")
            det[(truth, d["flagged"])] += 1
        out["conditions"][condition] = {
            "runs": len(cruns), "errors": sum(bool(r.get("error")) for r in cruns),
            "judge_calls": len(judges), "judge_final": dict(Counter(j["final"] for j in judges)),
            "judge_p0_human": sum(bool(j.get("by")) for j in judges),
            "judge_api_calls": sum(1 for j in judges if j.get("answer") and not j.get("cached")),
            "judge_input_tokens": sum(j.get("input_tokens", 0) for j in judges),
            "judge_cache_read_tokens": sum(j.get("cache_read_input_tokens", 0) for j in judges),
            "judge_cache_write_tokens": sum(j.get("cache_creation_input_tokens", 0) for j in judges),
            "judge_output_tokens": sum(j.get("output_tokens", 0) for j in judges),
            "escalated_by_policy": dict(escalated), "blocked_by_policy": dict(blocked_by_policy),
            "judge_final_by_policy": {f"{k[0]}→{k[1]}": v for k, v in final_by_policy.items()},
            "benign_lost": dict(lost), "benign_failed": sum(not r["utility"] for r in benign),
            "detector": {f"{t}/{'flagged' if f else 'not_flagged'}": n for (t, f), n in det.items()},
        }
    specs = {(r["suite"], r["user_task"]): json.dumps(r.get("spec"), sort_keys=True) for r in runs if r.get("spec")}
    per_task = Counter()
    for r in runs:
        if r.get("spec"):
            per_task[(r["suite"], r["user_task"], json.dumps(r["spec"], sort_keys=True))] += 1
    out["taskspec"] = {"tasks": len(specs),
                       "tasks_with_more_than_one_spec": sum(
                           1 for key in specs if len({s for (a, b, s) in per_task if (a, b) == key}) > 1)}
    return out


def detector_row(name: str) -> list | None:
    path = V2_DIR / "detector" / f"tool_outputs_{name}.json"
    if not path.exists():
        return None
    s = json.loads(path.read_text())
    o = s["overall"]
    return [name, s.get("mode", "-"), s.get("input_format", "-"), s["model"]["threshold"],
            pct(o["injected_flagged"], o["injected"]), pct(o["clean_flagged"], o["clean"])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", default=str(V2_DIR / "RESULTS.md"))
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    summary = json.loads((run_dir / "summary.json").read_text())
    significance = json.loads((run_dir / "significance.json").read_text())
    enforcement = json.loads((run_dir / "enforcement_check.json").read_text())
    run_args = json.loads(sorted(run_dir.glob("run_*.json"))[-1].read_text())["args"]
    analysis = analyse(run_dir)
    (run_dir / "v2_analysis.json").write_text(json.dumps(analysis, indent=1, ensure_ascii=False))
    conditions = list(summary["conditions"])
    rel = run_dir.relative_to(PROJECT_ROOT) if run_dir.is_relative_to(PROJECT_ROOT) else run_dir
    detectors = load_detector_config()["models"]
    attack_runs = summary["conditions"][conditions[0]]["attack_runs"]
    benign_runs = summary["conditions"][conditions[0]]["benign_runs"]

    lines = ["# v2 결과: 판별기 + Trusted LLM", "",
             f"> `scripts/v2/summarize_v2.py --run {rel}`가 생성한다. 손으로 고치지 않는다.", "",
             "## 1. 구성", ""]
    lines += [f"- 에이전트 `{summary['agent_model']}`, 공격 `{summary['attack']}`, AgentDojo v1.2.2, "
              f"조건마다 공격 없음 {benign_runs}건 + 공격 {attack_runs}건"
              f"{' (전체 규모)' if not run_args.get('pairs') and not run_args.get('pilot') else ''}.",
              "- TaskSpec: 실행 전에 작업마다 한 번 live 추출(`AGENTGATE_LLM_MODEL`), 같은 실행의 모든 조건이 같은 "
              f"TaskSpec을 쓴다(작업 {analysis['taskspec']['tasks']}개, 실행 간 TaskSpec이 다른 작업 "
              f"{analysis['taskspec']['tasks_with_more_than_one_spec']}개).",
              "- Trusted LLM v2 입력: TaskSpec + 사용자 고정 설정(`always_block`·`always_human`) + 대기 중 호출 + 판별 결과. "
              "HUMAN은 BLOCK으로 집행한다. P10은 ESCALATE만 낸다. 규칙 ④는 끈다.", ""]
    lines += table(["조건", "구성", "판별기 · 입력"], [
        [c, get_condition(c).description,
         (f"`{get_condition(c).detector}` · {detectors[get_condition(c).detector].get('mode')}, "
          f"{detectors[get_condition(c).detector].get('input_format', 'raw')}") if get_condition(c).detector else "-"]
        for c in conditions]) + [""]

    lines += ["## 2. 실제 에이전트", "",
              "비율 옆은 Wilson 95% CI, \"C0 대비\"는 같은 짝의 차이와 exact McNemar p다.", ""]
    rows = []
    for c in conditions:
        m = significance["conditions"][c]
        rows.append([c, metric_cell(m["asr"]), paired_cell(m["asr"]), metric_cell(m["utility"]),
                     paired_cell(m["utility"]), metric_cell(m["utility_under_attack"]),
                     paired_cell(m["utility_under_attack"])])
    lines += table(["조건", "ASR", "ASR C0 대비", "Utility", "Utility C0 대비", "공격 중 Utility", "공격 중 C0 대비"],
                   rows) + [""]
    lines += [f"집행 무결성: 실행 {enforcement.get('runs', '-')}건, 위반 {enforcement.get('violations', '-')}건, "
              f"오류 {sum(analysis['conditions'][c]['errors'] for c in conditions)}건.", ""]

    lines += ["## 3. 판정 LLM과 정상 작업 손실", ""]
    rows = []
    for c in conditions:
        a = analysis["conditions"][c]
        if not a["judge_calls"]:
            continue
        f = a["judge_final"]
        rows.append([c, a["judge_calls"], f.get("ALLOW", 0), f.get("BLOCK", 0), f.get("HUMAN", 0), a["judge_p0_human"],
                     ", ".join(f"{k} {v}" for k, v in sorted(a["escalated_by_policy"].items(), key=lambda x: -x[1]))])
    lines += table(["조건", "판정 호출", "ALLOW", "BLOCK", "HUMAN(→BLOCK)", "그중 P0 사람 확인", "ESCALATE 정책"], rows) + [""]
    rows = [[c, f"{analysis['conditions'][c]['benign_failed']}/{benign_runs}",
             ", ".join(f"{k} {v}" for k, v in sorted(analysis["conditions"][c]["benign_lost"].items(),
                                                       key=lambda x: -x[1])) or "-"] for c in conditions]
    lines += ["공격 없는 실행에서 user task가 실패한 원인(첫 차단 기준):", ""]
    lines += table(["조건", "실패", "원인"], rows) + [""]

    lines += ["## 4. 판별기", "", "live 실행의 툴 응답 단위(정답 라벨: 응답 안의 공격 표시 `<INFORMATION>`):", ""]
    rows = []
    for c in conditions:
        d = analysis["conditions"][c]["detector"]
        if not d:
            continue
        inj = d.get("injected/flagged", 0), d.get("injected/flagged", 0) + d.get("injected/not_flagged", 0)
        cb = d.get("clean_benign_run/flagged", 0), d.get("clean_benign_run/flagged", 0) + d.get(
            "clean_benign_run/not_flagged", 0)
        ca = d.get("clean_attack_run/flagged", 0), d.get("clean_attack_run/flagged", 0) + d.get(
            "clean_attack_run/not_flagged", 0)
        rows.append([c, get_condition(c).detector, pct(*inj), pct(*cb), pct(*ca)])
    lines += table(["조건", "판별기", "주입 응답 탐지", "정상 실행 응답 오탐", "공격 실행의 깨끗한 응답 오탐"], rows) + [""]
    offline = [r for r in (detector_row(n) for n in detectors) if r]
    if offline:
        lines += ["AgentDojo 정답 호출의 툴 응답(`scripts/v2/detector_tool_output_eval.py`, 중복 제거):", ""]
        lines += table(["판별기", "모드", "입력", "임계값", "주입 응답 탐지", "정상 응답 오탐"], offline) + [""]

    lines += ["## 5. 비용 (Trusted LLM)", ""]
    rows = [[c, a["judge_api_calls"], a["judge_input_tokens"], a["judge_cache_read_tokens"],
             a["judge_cache_write_tokens"], a["judge_output_tokens"]]
            for c, a in analysis["conditions"].items() if a["judge_calls"]]
    lines += table(["조건", "API 호출", "입력(캐시 외)", "캐시 읽기", "캐시 쓰기", "출력"], rows) + [""]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out} and {run_dir / 'v2_analysis.json'}")


if __name__ == "__main__":
    main()
