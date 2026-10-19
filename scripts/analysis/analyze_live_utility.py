"""live run에서 게이트 조건의 utility 손실 원인을 C0(방어 없음)와 짝지어 분해한다.

같은 (suite, user task, injection task)에서 C0는 성공하고 대상 조건은 실패한 run을 "차단 있음 / 없음"으로 나누고,
차단된 호출마다 원인(게이트 BLOCK 정책, 판정 LLM BLOCK·HUMAN)과 역할(사용자 작업 툴 / 공격 툴)을 센다.

사용법: python scripts/analysis/analyze_live_utility.py --run results/v3/live/<run> \
          --baseline results/v3/live/<run> --condition C10a
run 폴더에 <condition>/가 없으면 runs.tar.gz를 임시 폴더에 풀어 읽는다. 출력: <run>/utility_analysis_<condition>.json
"""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

from agentgate.agentdojo.suites import load_suite
from agentgate.config import PROJECT_ROOT


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
    return f"judge_{judge['final'].lower()}[{escalated}]"


def analyze(runs: dict, baseline: dict) -> dict:
    out: dict = {}
    for kind in ("benign", "attack"):
        keys = sorted(k for k in runs if (k[2] is None) == (kind == "benign") and k in baseline)
        pairing, causes, causes_by_role = Counter(), Counter(), Counter()
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
        out[kind] = {"pairs": len(keys), "pairing": dict(sorted(pairing.items())),
                     "blocked_calls_in_lost_runs": sum(causes.values()),
                     "causes": dict(causes.most_common()), "causes_by_role": dict(causes_by_role.most_common())}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--baseline", required=True, help="run directory holding C0 (same pairs)")
    parser.add_argument("--condition", required=True, help="condition to analyse, e.g. C10a")
    args = parser.parse_args()
    run_dir, baseline_dir = PROJECT_ROOT / args.run, PROJECT_ROOT / args.baseline
    with tempfile.TemporaryDirectory() as tmp:
        runs = load_condition(run_dir, args.condition, Path(tmp))
        baseline = load_condition(baseline_dir, "C0", Path(tmp) / "baseline")
    result = {"run": args.run, "baseline": args.baseline, "condition": args.condition, **analyze(runs, baseline)}
    (run_dir / f"utility_analysis_{args.condition}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    for kind in ("benign", "attack"):
        print(kind, result[kind]["pairing"])
        print("  blocked calls in lost runs", result[kind]["blocked_calls_in_lost_runs"])


if __name__ == "__main__":
    main()
