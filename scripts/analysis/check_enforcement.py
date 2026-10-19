"""live run의 차단 무결성을 검사한다. run별 검사는 `agentgate.harness.enforcement.check_run`이다.

run별 검사 항목:
  0. 정책 Gate를 거치지 않은 툴 실행(ungated_calls)이 없는가
  1. blocked=True인 호출이 실행되지 않았는가
  2. 차단된 호출 전후로 환경이 바뀌지 않았는가 (차단 호출 직전 hash == 다음 gate event 직전 hash,
     마지막 호출이면 최종 hash)
  3. 에이전트에 반환된 툴 결과와 gate event가 1:1로 대응하는가 (같은 툴, 같은 순서). AgentDojo가 실행 전에
     거부하는 호출(알 수 없는 툴·빈 이름)은 제외하고, ToolsExecutionLoop 반복 한도에서 요청된 호출은 실행되지
     않으므로 따로 센다.

사용법: python scripts/analysis/check_enforcement.py --run results/live/<name>
출력: <run>/enforcement_check.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agentgate.config import PROJECT_ROOT
from agentgate.experiment.live_run_analysis import live_run_files
from agentgate.harness.enforcement import check_run, unexecuted_requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="results/live/<name>")
    args = parser.parse_args()
    run_dir = PROJECT_ROOT / args.run
    files = live_run_files(run_dir)
    runs_by_condition, violations = Counter(), []
    stats = Counter()
    for path in files:
        run = json.loads(path.read_text())
        if run.get("error"):
            stats["errored_runs"] += 1
            continue
        runs_by_condition[run["condition"]] += 1
        stats["runs"] += 1
        stats["gate_events"] += len(run["trace"])
        stats["blocked_events"] += sum(e["blocked"] for e in run["trace"])
        unexecuted_count = unexecuted_requests(run)
        stats["runs_with_unexecuted_requests_at_loop_limit"] += unexecuted_count > 0
        for issue in check_run(run):
            violations.append({"file": str(path.relative_to(PROJECT_ROOT)), **issue})
    result = {"runs_by_condition": dict(runs_by_condition), **stats, "violations": len(violations),
              "violations_by_check": dict(Counter(v["check"] for v in violations)), "examples": violations[:50]}
    (run_dir / "enforcement_check.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}, indent=1))


if __name__ == "__main__":
    main()
