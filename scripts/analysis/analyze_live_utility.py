"""live run에서 게이트 조건의 utility 손실 원인을 C0(방어 없음)와 짝지어 분해한다.

같은 (suite, user task, injection task)에서 C0는 성공하고 대상 조건은 실패한 run을 "차단 있음 / 없음"으로 나누고,
차단된 호출마다 원인(게이트 BLOCK 정책, 판정 LLM BLOCK·HUMAN, ④, P0)과 역할(사용자 작업 툴 / 공격 툴)을 센다.
판별기 오탐은 정상(주입 없음) run의 응답 중 flagged 비율로 본다.

사용법: python scripts/analysis/analyze_live_utility.py --run results/live/v2/strat3_tk_gpt-oss_120b \
          --baseline results/live/v1/strat3_tk_gpt-oss_120b --condition C5
run 폴더에 <condition>/가 없으면 runs.tar.gz를 임시 폴더에 풀어 읽는다. 출력: <run>/utility_analysis_<condition>.json
"""

from __future__ import annotations

import argparse
import json
import re
import tarfile
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

from agentgate.config import PROJECT_ROOT
from agentgate.suites import load_suite


def load_condition(run_dir: Path, condition: str, tmp: Path) -> dict:
    root = run_dir
    if not (run_dir / condition).exists():
        root = tmp / run_dir.name
        with tarfile.open(run_dir / "runs.tar.gz") as archive:
            archive.extractall(root, filter="data")
    runs = {}
    for path in (root / condition).glob("*/*/*.json"):
        run = json.loads(path.read_text())
        runs[(run["suite"], run["user_task"], run["injection_task"])] = run
    return runs


@lru_cache(maxsize=None)
def ground_truth_tools(suite_name: str, task_id: str, injection: bool) -> frozenset:
    suite = load_suite(suite_name)
    task = (suite.get_injection_task_by_id if injection else suite.get_user_task_by_id)(task_id)
    environment = suite.load_and_inject_default_environment({})
    if not injection:
        environment = task.init_environment(environment)
    return frozenset(call.function for call in task.ground_truth(environment))


def block_cause(event: dict) -> str:
    judge = event.get("judge") or {}
    if not judge:
        return "gate_block:" + "+".join(sorted({d["policy_id"] for d in event["decisions"] if d["verdict"] == "BLOCK"}))
    escalated = "+".join(sorted({d["policy_id"] for d in event["decisions"] if d["verdict"] == "ESCALATE"}))
    if judge.get("by"):
        return "p0_human"
    if judge.get("rule4"):
        return f"rule4_human[{escalated}]"
    return f"judge_{judge['final'].lower()}[{escalated}]"


def p10_chunk(event: dict) -> str | None:
    reason = next((d["reason"] for d in event["decisions"] if d["policy_id"] == "P10" and d["verdict"] != "ALLOW"),
                  None)
    if reason is None:
        return None
    match = re.search(r"value chunk ([0-9.]+)", reason)
    if not match:
        return "other_tool_suspicious_chunk"
    return "clean_chunk" if float(match.group(1)) < 0.5 else "suspicious_chunk"


def analyze(runs: dict, baseline: dict) -> dict:
    out: dict = {}
    for kind in ("benign", "attack"):
        keys = sorted(k for k in runs if (k[2] is None) == (kind == "benign") and k in baseline)
        pairing, causes, causes_by_role, p10_chunks = Counter(), Counter(), Counter(), Counter()
        for key in keys:
            run, base = runs[key], baseline[key]
            blocks = [e for e in run["trace"] if e["blocked"]]
            pairing[f"C0={'ok' if base['utility'] else 'fail'} cond={'ok' if run['utility'] else 'fail'} "
                    f"{'blocked' if blocks else 'no_block'}"] += 1
            if not (base["utility"] and not run["utility"] and blocks):
                continue
            user_tools = ground_truth_tools(key[0], key[1], False)
            attack_tools = ground_truth_tools(key[0], key[2], True) if key[2] else frozenset()
            for event in blocks:
                role = ("user_task_tool" if event["tool"] in user_tools
                        else "attack_tool" if event["tool"] in attack_tools else "other")
                cause = block_cause(event)
                causes[cause] += 1
                causes_by_role[f"{cause} | {role}"] += 1
                chunk = p10_chunk(event)
                if chunk and role == "user_task_tool":
                    p10_chunks[chunk] += 1
        involving_p10 = sum(v for c, v in causes.items() if "P10" in c)
        out[kind] = {"pairs": len(keys), "pairing": dict(sorted(pairing.items())),
                     "blocked_calls_in_lost_runs": sum(causes.values()),
                     "blocked_calls_involving_p10": involving_p10,
                     "causes": dict(causes.most_common()), "causes_by_role": dict(causes_by_role.most_common()),
                     "p10_user_task_calls_by_value_chunk": dict(p10_chunks)}
    flagged, total, by_tool = 0, 0, Counter()
    for key, run in runs.items():
        if key[2] is not None:
            continue
        for event in run["trace"]:
            detection = event.get("detection")
            if detection:
                total += 1
                flagged += detection["flagged"]
                by_tool[f"{key[0]}/{event['tool']}"] += detection["flagged"]
    out["detector_on_benign_runs"] = {"responses": total, "flagged": flagged,
                                      "flagged_by_tool": dict(by_tool.most_common(12))}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--baseline", required=True, help="run directory holding C0 (same pairs)")
    parser.add_argument("--condition", default="C5")
    args = parser.parse_args()
    run_dir, baseline_dir = PROJECT_ROOT / args.run, PROJECT_ROOT / args.baseline
    with tempfile.TemporaryDirectory() as tmp:
        runs = load_condition(run_dir, args.condition, Path(tmp))
        baseline = load_condition(baseline_dir, "C0", Path(tmp) / "baseline")
    result = {"run": args.run, "baseline": args.baseline, "condition": args.condition, **analyze(runs, baseline)}
    (run_dir / f"utility_analysis_{args.condition}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    for kind in ("benign", "attack"):
        print(kind, result[kind]["pairing"])
        print("  blocked calls in lost runs", result[kind]["blocked_calls_in_lost_runs"],
              "involving P10", result[kind]["blocked_calls_involving_p10"],
              "P10 user-task calls by chunk", result[kind]["p10_user_task_calls_by_value_chunk"])
    detector = result["detector_on_benign_runs"]
    print("detector on benign runs: flagged", detector["flagged"], "/", detector["responses"])


if __name__ == "__main__":
    main()
