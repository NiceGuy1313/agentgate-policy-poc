"""oracle TaskSpec과 LLM TaskSpec을 필드별로 비교한다.

출력: data/specs/llm/<model>/comparison.json, comparison.md

사용법: python scripts/taskspec/compare_specs.py --model <model>
"""

from __future__ import annotations

import argparse
import json

from agentgate import SUITES
from agentgate.config import PROJECT_ROOT
from agentgate.normalization import normalize_value
from agentgate.task_spec import TaskSpec


def _normalized_set(values):
    return {normalize_value(v) for v in values}


def compare(model: str) -> dict:
    oracle_root = PROJECT_ROOT / "data" / "specs" / "oracle"
    llm_root = PROJECT_ROOT / "data" / "specs" / "llm" / model
    true_positives = false_positives = false_negatives = 0
    rows, agree = [], {"allowed_dest": 0, "named_sources": 0, "amount_cap": 0, "egress_requested": 0}
    compared = missing = 0
    for suite in SUITES:
        for oracle_path in sorted((oracle_root / suite).glob("*.yaml"), key=lambda p: int(p.stem.split("_")[-1])):
            task = f"{suite}/{oracle_path.stem}"
            llm_path = llm_root / suite / oracle_path.name
            if not llm_path.exists():
                missing += 1
                rows.append({"task": task, "field": "*", "kind": "누락(추출 실패)", "oracle": "", "llm": ""})
                continue
            oracle, llm = TaskSpec.load(oracle_path), TaskSpec.load(llm_path)
            compared += 1
            oracle_ops, llm_ops = set(oracle.requested_ops), set(llm.requested_ops)
            true_positives += len(oracle_ops & llm_ops)
            false_positives += len(llm_ops - oracle_ops)
            false_negatives += len(oracle_ops - llm_ops)
            if oracle_ops - llm_ops:
                rows.append({"task": task, "field": "requested_ops", "kind": "누락",
                             "oracle": sorted(oracle_ops - llm_ops), "llm": ""})
            if llm_ops - oracle_ops:
                rows.append({"task": task, "field": "requested_ops", "kind": "과잉",
                             "oracle": "", "llm": sorted(llm_ops - oracle_ops)})
            for field in ("allowed_dest", "named_sources"):
                oracle_set, llm_set = _normalized_set(getattr(oracle, field)), _normalized_set(getattr(llm, field))
                raw_equal = set(getattr(oracle, field)) == set(getattr(llm, field))
                if oracle_set == llm_set:
                    agree[field] += 1
                    if not raw_equal:
                        rows.append({"task": task, "field": field, "kind": "형식 차이",
                                     "oracle": getattr(oracle, field), "llm": getattr(llm, field)})
                else:
                    kind = "누락" if llm_set < oracle_set else "과잉" if llm_set > oracle_set else "불일치"
                    rows.append({"task": task, "field": field, "kind": kind,
                                 "oracle": sorted(oracle_set - llm_set), "llm": sorted(llm_set - oracle_set)})
            if oracle.amount_cap == llm.amount_cap:
                agree["amount_cap"] += 1
            else:
                rows.append({"task": task, "field": "amount_cap", "kind": "불일치",
                             "oracle": oracle.amount_cap, "llm": llm.amount_cap})
            if oracle.egress_requested == llm.egress_requested:
                agree["egress_requested"] += 1
            else:
                rows.append({"task": task, "field": "egress_requested", "kind": "불일치",
                             "oracle": oracle.egress_requested, "llm": llm.egress_requested})
    precision = (true_positives / (true_positives + false_positives)
                 if true_positives + false_positives else None)
    recall = true_positives / (true_positives + false_negatives) if true_positives + false_negatives else None
    return {"model": model, "n_compared": compared, "missing": missing,
            "requested_ops": {"tp": true_positives, "fp": false_positives, "fn": false_negatives,
                              "precision": precision, "recall": recall},
            "agreement": {k: v / compared if compared else None for k, v in agree.items()},
            "agreement_counts": agree, "mismatches": rows}


def _format_percentage(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    model_dir = PROJECT_ROOT / "data" / "specs" / "llm" / args.model
    if not model_dir.exists():
        raise SystemExit(f"no LLM specs for {args.model}; run scripts/taskspec/extract_llm_specs.py first")
    result = compare(args.model)
    (model_dir / "comparison.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    ops = result["requested_ops"]
    lines = [
        "# oracle vs LLM TaskSpec 비교", "",
        f"이 문서는 `scripts/taskspec/compare_specs.py --model {args.model}` 가 생성한다. "
        "oracle은 검토 완료본(`status: reviewed`)을 기준으로 한다.", "",
        "| 지표 | 값 |", "|---|---|",
        f"| 비교한 태스크 수 | {result['n_compared']} (추출 실패 {result['missing']}) |",
        f"| requested_ops 정밀도 | {_format_percentage(ops['precision'])} (TP {ops['tp']}, FP {ops['fp']}) |",
        f"| requested_ops 재현율 | {_format_percentage(ops['recall'])} (FN {ops['fn']}) |",
    ]
    for field, rate in result["agreement"].items():
        lines.append(f"| {field} 일치율 | {_format_percentage(rate)} "
                     f"({result['agreement_counts'][field]}/{result['n_compared']}) |")
    lines += ["", "## 불일치 사례", "", "| 태스크 | 필드 | 유형 | oracle에만 | LLM에만 |", "|---|---|---|---|---|"]
    for mismatch in result["mismatches"]:
        lines.append(f"| {mismatch['task']} | {mismatch['field']} | {mismatch['kind']} | "
                     f"{mismatch['oracle']} | {mismatch['llm']} |")
    (model_dir / "comparison.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "mismatches"}, indent=1))


if __name__ == "__main__":
    main()
