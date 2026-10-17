"""v2 결과 요약을 results/v2/RESULTS.md로 만든다. 수치는 모두 실행 기록에서 계산한다(손으로 고치지 않는다).

선행: check_enforcement.py, summarize_live.py, live_significance.py를 같은 --run에 실행
입력: <run>/{C0,C5,C5p}/... 실행 기록, <run>/{summary,significance,enforcement_check,run_*}.json,
      results/v2/detector/tool_outputs_*.json(오프라인 판별기 평가), config/v2/detector.yaml
출력: results/v2/RESULTS.md (--cleanup이면 그 뒤 실행 기록·중간 json·로그를 지우고 RESULTS.md만 남긴다)

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
LABELS = {"C0": "방어 없음", "C5": "horizon 모델(IPI detector)", "C5p": "PIGuard 모델(IPI detector)"}
FLOW = """## 검사 흐름

```mermaid
flowchart TD
    P[사용자 프롬프트 + 툴 목록] --> TS[TaskSpec 추출<br/>claude-haiku-5-5, 실행 전 작업마다 1회]
    TS --> G
    P --> A[에이전트 LLM<br/>gpt-oss:120b]
    A -->|툴 호출| G{정책 엔진<br/>P0 + P1–P9 + P10}
    G -->|ALLOW| X[툴 실행]
    G -->|BLOCK| B[차단 메시지를 툴 결과로 반환]
    G -->|ESCALATE| T[Trusted LLM<br/>claude-sonnet-5-5]
    T -->|ALLOW| X
    T -->|BLOCK · HUMAN| B
    X --> D[IPI detector<br/>horizon 모델 또는 PIGuard 모델]
    D -->|응답 라벨: 창별 점수| G
    X -->|툴 응답| A
    B --> A
```

| 구성 요소 | 입력 | 출력 |
|---|---|---|
| TaskSpec 추출 (claude-haiku-5-5, Message Batches) | 사용자 프롬프트, 툴 목록(이름·설명). 툴 응답·정답·공격 목표는 넣지 않는다. 프롬프트 `prompts/v1/extract_taskspec.md` | TaskSpec JSON: `requested_ops`(요청된 상태 변경·외부 전송 툴), `named_sources`(사용자가 지목한 자료), `allowed_dest`(프롬프트의 목적지), `amount_cap`(송금 상한), `egress_requested`(데이터 전송 요청 여부), `notes` |
| 에이전트 LLM (gpt-oss:120b) | 사용자 프롬프트, 이전 툴 응답(차단 메시지 포함) | 툴 호출(툴 이름, 인자) 또는 최종 답 |
| 정책 엔진 (`GatedRuntime`, LLM 없음) | 툴 호출, TaskSpec, 사용자 고정 설정(`always_block: []`, `always_human: [delete_file, update_password]`), 값 출처 인덱스(프롬프트와 이전 툴 응답), 응답별 IPI detector 라벨 | 정책별 ALLOW / ESCALATE / BLOCK, 가장 강한 판정을 최종으로. P0: `always_block` → BLOCK, `always_human` → 사람 확인(판정 LLM 없이 HUMAN). P1–P9: v1 정책. P10: 인자 값이 detector가 flag한 응답(또는 그 응답의 의심 창)에서 왔으면 ESCALATE(BLOCK은 내지 않음) |
| IPI detector | 실행된 툴 응답. horizon 모델: 원문(에이전트가 받는 YAML) 2048토큰 창·512토큰 겹침. PIGuard 모델: 레코드별 평문(키·기호 제거) 512토큰 창·128토큰 겹침 | 창별 주입 확률, 응답 점수(창 최고값), flagged(점수 ≥ 0.5). 정책 엔진에는 이 라벨만 넘긴다 |
| Trusted LLM (claude-sonnet-5-5) | ① 허용 범위: TaskSpec, 사용자 고정 설정. ② 대기 중 호출: 툴, 되돌림 가능 여부, 인자 값(400자 절단)과 값마다의 출처(프롬프트·지목 자료·그 밖의 툴 응답·출처 없음). ③ 판별 결과: 값이 나온 응답의 점수·flagged·의심 창 수, 값이 속한 창 점수, 의심 창 발췌 최대 3개(240자). 사용자 프롬프트 원문, ESCALATE 이유, 툴 응답 전문은 넣지 않는다 | 첫 줄 ALLOW / BLOCK / HUMAN과 한 문장 근거. 해석 실패는 HUMAN. HUMAN은 BLOCK으로 집행한다 |
| 차단 | BLOCK 판정 | 툴을 실행하지 않고 `Tool call blocked by security policy <정책>: <이유>`를 툴 결과로 에이전트에 돌려준다 |

"""


def label(condition: str) -> str:
    return LABELS.get(condition, condition)


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


def _policy_block(event: dict) -> str:
    ids = sorted({d["policy_id"] for d in event["decisions"] if d["verdict"] == "BLOCK"})
    return "정책 BLOCK " + ",".join(ids) if ids else "ESCALATE→BLOCK"


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
        lost, attack_lost = Counter(), Counter()
        for r in [r for r in cruns if r["injection_task"] is not None]:
            if r["utility"]:
                continue
            blocks = [e for e in r["trace"] if e.get("blocked")]   # enforced only (C0 audit never blocks)
            if not blocks:
                attack_lost["실패, 차단 없음"] += 1
                continue
            e = blocks[0]
            attack_lost[f"판정 LLM {e['judge']['final']}" if e.get("judge") else
                        _policy_block(e)] += 1
        for r in benign:
            if r["utility"]:
                continue
            blocks = [e for e in r["trace"] if e.get("blocked")]   # enforced only (C0 audit never blocks)
            if not blocks:
                lost["실패, 차단 없음"] += 1
                continue
            e = blocks[0]
            lost[f"판정 LLM {e['judge']['final']}" if e.get("judge") else
                 _policy_block(e)] += 1
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
            "attack_lost": dict(attack_lost),
            "attack_failed": sum(not r["utility"] for r in cruns if r["injection_task"] is not None),
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


def detector_rows() -> list[list]:
    """Offline detector evaluations: the configured inputs (tool_outputs_*) first, then alternatives (alt_*)."""
    rows = []
    for pattern, kind in (("tool_outputs_*.json", "설정"), ("alt_*.json", "비교")):
        for path in sorted((V2_DIR / "detector").glob(pattern)):
            s = json.loads(path.read_text())
            o = s["overall"]
            rows.append([s["detector"], kind, s.get("mode", "-"), s.get("input_format", "-"), s["model"]["threshold"],
                         pct(o["injected_flagged"], o["injected"]), pct(o["clean_flagged"], o["clean"])])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", default=str(V2_DIR / "RESULTS.md"))
    parser.add_argument("--cleanup", action="store_true", help="keep only RESULTS.md afterwards")
    args = parser.parse_args()
    run_dir = Path(args.run).resolve()
    summary = json.loads((run_dir / "summary.json").read_text())
    significance = json.loads((run_dir / "significance.json").read_text())
    enforcement = json.loads((run_dir / "enforcement_check.json").read_text())
    run_args = json.loads(sorted(run_dir.glob("run_*.json"))[-1].read_text())["args"]
    analysis = analyse(run_dir)
    conditions = list(summary["conditions"])
    rel = run_dir.relative_to(PROJECT_ROOT) if run_dir.is_relative_to(PROJECT_ROOT) else run_dir
    detectors = load_detector_config()["models"]
    attack_runs = summary["conditions"][conditions[0]]["attack_runs"]
    benign_runs = summary["conditions"][conditions[0]]["benign_runs"]

    lines = ["# v2 결과: 판별기 + Trusted LLM", "",
             f"> `scripts/v2/summarize_v2.py --run {rel}`가 생성한다. 손으로 고치지 않는다.", "",
             *FLOW.rstrip("\n").split("\n"), "", "## 1. 구성", ""]
    lines += [f"- 에이전트 `{summary['agent_model']}`, 공격 `{summary['attack']}`, AgentDojo v1.2.2, "
              f"조건마다 공격 없음 {benign_runs}건 + 공격 {attack_runs}건"
              f"{' (전체 규모)' if not run_args.get('pairs') and not run_args.get('pilot') else ''}.",
              "- TaskSpec: 실행 전에 작업마다 한 번 live 추출(`AGENTGATE_LLM_MODEL`), 같은 실행의 모든 조건이 같은 "
              f"TaskSpec을 쓴다(작업 {analysis['taskspec']['tasks']}개, 실행 간 TaskSpec이 다른 작업 "
              f"{analysis['taskspec']['tasks_with_more_than_one_spec']}개).",
              "- Trusted LLM v2 입력: TaskSpec + 사용자 고정 설정(`always_block`·`always_human`) + 대기 중 호출 + 판별 결과. "
              "HUMAN은 BLOCK으로 집행한다. P10은 ESCALATE만 낸다. 규칙 ④는 끈다.", ""]
    lines += table(["조건", "구성", "판별기 · 입력"], [
        [label(c), get_condition(c).description,
         (f"`{get_condition(c).detector}` · {detectors[get_condition(c).detector].get('mode')}, "
          f"{detectors[get_condition(c).detector].get('input_format', 'raw')}") if get_condition(c).detector else "-"]
        for c in conditions]) + [""]

    lines += ["## 2. 실제 에이전트", "",
              "비율 옆은 Wilson 95% CI, \"방어 없음 대비\"는 같은 짝의 차이와 exact McNemar p다.", ""]
    rows = []
    for c in conditions:
        m = significance["conditions"][c]
        rows.append([label(c), metric_cell(m["asr"]), paired_cell(m["asr"]), metric_cell(m["utility"]),
                     paired_cell(m["utility"]), metric_cell(m["utility_under_attack"]),
                     paired_cell(m["utility_under_attack"])])
    lines += table(["조건", "ASR", "ASR 방어 없음 대비", "Utility", "Utility 방어 없음 대비", "공격 중 Utility", "공격 중 방어 없음 대비"],
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
        rows.append([label(c), a["judge_calls"], f.get("ALLOW", 0), f.get("BLOCK", 0), f.get("HUMAN", 0), a["judge_p0_human"],
                     ", ".join(f"{k} {v}" for k, v in sorted(a["escalated_by_policy"].items(), key=lambda x: -x[1]))])
    lines += table(["조건", "판정 호출", "ALLOW", "BLOCK", "HUMAN(→BLOCK)", "그중 P0 사람 확인", "ESCALATE 정책"], rows) + [""]
    rows = [[label(c), f"{analysis['conditions'][c]['benign_failed']}/{benign_runs}",
             ", ".join(f"{k} {v}" for k, v in sorted(analysis["conditions"][c]["benign_lost"].items(),
                                                       key=lambda x: -x[1])) or "-"] for c in conditions]
    lines += ["공격 없는 실행에서 user task가 실패한 원인(첫 차단 기준):", ""]
    lines += table(["조건", "실패", "원인"], rows) + [""]
    rows = [[label(c), f"{analysis['conditions'][c]['attack_failed']}/{attack_runs}",
             ", ".join(f"{k} {v}" for k, v in sorted(analysis["conditions"][c]["attack_lost"].items(),
                                                       key=lambda x: -x[1])[:6]) or "-"] for c in conditions]
    lines += ["공격 실행에서 user task가 실패한 원인(첫 차단 기준, 상위 6개):", ""]
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
        rows.append([label(c), get_condition(c).detector, pct(*inj), pct(*cb), pct(*ca)])
    lines += table(["조건", "판별기", "주입 응답 탐지", "정상 실행 응답 오탐", "공격 실행의 깨끗한 응답 오탐"], rows) + [""]
    offline = detector_rows()
    if offline:
        lines += ["AgentDojo 정답 호출의 툴 응답(`scripts/v2/detector_tool_output_eval.py`, 중복 제거). "
                  "\"설정\"은 실험에 쓴 입력, \"비교\"는 다른 입력 형식:", ""]
        lines += table(["판별기", "구분", "모드", "입력", "임계값", "주입 응답 탐지", "정상 응답 오탐"], offline) + [""]

    lines += ["## 5. 비용 (Trusted LLM)", ""]
    rows = [[label(c), a["judge_api_calls"], a["judge_input_tokens"], a["judge_cache_read_tokens"],
             a["judge_cache_write_tokens"], a["judge_output_tokens"]]
            for c, a in analysis["conditions"].items() if a["judge_calls"]]
    lines += table(["조건", "API 호출", "입력(캐시 외)", "캐시 읽기", "캐시 쓰기", "출력"], rows) + [""]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out}")
    if args.cleanup:
        import shutil
        shutil.rmtree(run_dir)
        for path in (V2_DIR / "detector").glob("*.json"):
            path.unlink()
        for path in (V2_DIR / "detector", V2_DIR / "live"):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        print(f"removed {rel} and results/v2/detector/*.json")


if __name__ == "__main__":
    main()
