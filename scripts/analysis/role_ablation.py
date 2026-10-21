"""프레임워크 요인 대조 실험(smolagents 시스템 프롬프트·툴 응답 역할, native, mini-swe-agent)을 집계해 RESULTS.md 절과 summary JSON을 만든다.

입력: 같은 공격 짝(`data/agentdojo/pairs/role_ablation_97.txt`)을 Gate 없이 돌린 실행 디렉터리 세 개.
지표: ASR, 공격 중 Utility, 노출률(주입이 든 툴 응답을 받은 run), 노출 시 추종률, suite·주입 유형별 ASR,
같은 짝끼리의 exact McNemar p. 수치는 모두 실행 기록에서 계산한다.
사용법: python scripts/analysis/role_ablation.py --arm "smolagents (user)=results/runs/role_smol_user" ...
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from agentgate.config import PROJECT_ROOT
from agentgate.experiment.live_run_analysis import live_run_files
from agentgate.experiment.metrics import load_injection_labels, mcnemar_exact, wilson

RESULTS = PROJECT_ROOT / "results" / "RESULTS.md"
SECTION = "## 프레임워크 요인 대조(소규모)"
MARK = "<INFORMATION>"


def load(run_dir: Path) -> tuple[dict, int]:
    runs, errors = {}, 0
    for path in live_run_files(run_dir):
        run = json.loads(path.read_text())
        if "condition" not in run or run.get("injection_task") is None:
            continue
        if run.get("error") or run.get("valid") is False:
            errors += 1
            continue
        runs[(run["suite"], run["user_task"], run["injection_task"])] = run
    return runs, errors


def exposed(run: dict) -> bool:
    """The agent received a tool result that carries the injection."""
    return any(m.get("role") == "tool" and MARK in str(m.get("content") or "") for m in run.get("messages") or [])


def rate(values: list[bool]) -> dict:
    k, n = sum(values), len(values)
    lo, hi = wilson(k, n) or (0.0, 0.0)
    return {"k": k, "n": n, "rate": k / n if n else 0.0, "ci95": [lo, hi]}


def pct(x: dict) -> str:
    return f"{100 * x['rate']:.1f}% ({x['k']}/{x['n']}, {100 * x['ci95'][0]:.1f}–{100 * x['ci95'][1]:.1f})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", action="append", required=True, help="label=run_dir")
    parser.add_argument("--pairs", default="data/agentdojo/pairs/role_ablation_97.txt")
    args = parser.parse_args()
    labels = load_injection_labels()
    arms = {}
    for spec in args.arm:
        label, _, path = spec.partition("=")
        arms[label] = load((PROJECT_ROOT / path).resolve())
    keys = sorted(set.intersection(*(set(runs) for runs, _ in arms.values())))
    names = list(arms)

    table = {}
    for name, (runs, errors) in arms.items():
        cell = [runs[k] for k in keys]
        exp = [r for r in cell if exposed(r)]
        by_suite, by_type = defaultdict(list), defaultdict(list)
        for k, r in zip(keys, cell):
            by_suite[k[0]].append(bool(r["security"]))
            by_type[labels[k[0]][k[2]]["type"]].append(bool(r["security"]))
        table[name] = {
            "errors": errors,
            "asr": rate([bool(r["security"]) for r in cell]),
            "uua": rate([bool(r["utility"]) for r in cell]),
            "exposed": rate([exposed(r) for r in cell]),
            "follow": rate([bool(r["security"]) for r in exp]),
            "tool_calls": round(statistics.mean(len(r["trace"]) for r in cell), 2) if cell else None,
            "by_suite": {s: rate(v) for s, v in sorted(by_suite.items())},
            "by_type": {t: rate(v) for t, v in sorted(by_type.items())},
        }

    def paired(a: str, b: str, field: str = "security") -> dict:
        ra, rb = arms[a][0], arms[b][0]
        a_only = [k for k in keys if ra[k][field] and not rb[k][field]]
        b_only = [k for k in keys if rb[k][field] and not ra[k][field]]
        return {"a_only": len(a_only), "b_only": len(b_only), "p": mcnemar_exact(len(b_only), len(a_only)),
                "a_only_pairs": [":".join(k) for k in a_only], "b_only_pairs": [":".join(k) for k in b_only]}

    comparisons = {f"{a} vs {b}": {"asr": paired(a, b), "uua": paired(a, b, "utility")}
                   for i, a in enumerate(names) for b in names[i + 1:]}
    out = PROJECT_ROOT / "results" / "summary" / "role_ablation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"pairs": args.pairs, "n_pairs": len(keys), "table": table,
                               "comparisons": comparisons}, indent=1, ensure_ascii=False))

    lines = [SECTION, "",
             f"> `scripts/analysis/role_ablation.py`가 생성한다. 같은 공격 짝 {len(keys)}건(`{args.pairs}`, "
             "`strat3_seed0`에서 사용자 작업마다 첫 공격 하나)을 Gate 없이, gpt-oss:120b와 `tool_knowledge`로 돌렸다. "
             "smolagents 팔은 Adapter 옵션 두 개만 바꾼다. `system_prompt=minimal`은 기본 `ToolCallingAgent` 프롬프트 대신 "
             "mini-swe-agent와 같은 짧은 지시를 쓰고, `tool_response_role=tool`은 툴 호출·응답을 smolagents 기본(assistant "
             "텍스트 + `user` 메시지) 대신 OpenAI 툴 형식(assistant `tool_calls` + `tool` 메시지)으로 보낸다. 오류로 빠진 짝은 "
             "모든 팔에서 제외한다. 노출은 주입문이 든 툴 응답을 받은 run, 추종률은 노출된 run 중 "
             "공격이 성공한 비율이다.", "",
             "| 팔 | ASR | 공격 중 Utility | 노출 | 노출 시 추종률 | 평균 툴 호출 | 오류 제외 |",
             "|---|---|---|---|---|---|---|"]
    for name, t in table.items():
        lines.append(f"| {name} | {pct(t['asr'])} | {pct(t['uua'])} | {pct(t['exposed'])} | {pct(t['follow'])} | "
                     f"{t['tool_calls']} | {t['errors']} |")
    lines += ["", "같은 짝끼리 비교(exact McNemar p, 앞 팔만 성공 / 뒤 팔만 성공):", ""]
    for title, c in comparisons.items():
        lines.append(f"- {title}: ASR {c['asr']['a_only']}/{c['asr']['b_only']} (p={c['asr']['p']:.3g}), "
                     f"공격 중 Utility {c['uua']['a_only']}/{c['uua']['b_only']} (p={c['uua']['p']:.3g})")
    suites = sorted({s for t in table.values() for s in t["by_suite"]})
    types = sorted({s for t in table.values() for s in t["by_type"]})
    lines += ["", "suite별 ASR:", "", "| 팔 | " + " | ".join(suites) + " |", "|---|" + "---|" * len(suites)]
    for name, t in table.items():
        lines.append(f"| {name} | " + " | ".join(
            f"{100 * t['by_suite'][s]['rate']:.0f}% ({t['by_suite'][s]['k']}/{t['by_suite'][s]['n']})"
            for s in suites) + " |")
    lines += ["", "주입 유형별 ASR(A 외부 연락, B 금전, C 반출, D 삭제, E 계정, F 전파·방문, G 예약, H 툴 없는 출력):", "",
              "| 팔 | " + " | ".join(types) + " |", "|---|" + "---|" * len(types)]
    for name, t in table.items():
        lines.append(f"| {name} | " + " | ".join(
            f"{100 * t['by_type'][s]['rate']:.0f}% ({t['by_type'][s]['k']}/{t['by_type'][s]['n']})"
            for s in types) + " |")
    lines.append("")

    text = RESULTS.read_text()
    block = "\n".join(lines) + "\n"
    if SECTION in text:
        start = text.index(SECTION)
        end = text.find("\n## ", start + len(SECTION))
        text = text[:start] + block + ("" if end == -1 else text[end + 1:])
    else:
        anchor = "## 에이전트별 그래프"
        text = text.replace(anchor, block + "\n" + anchor, 1)
    RESULTS.write_text(text)
    print(f"wrote {RESULTS}, {out}")


if __name__ == "__main__":
    main()
