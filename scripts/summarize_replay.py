"""Stage 5 tasks 2-5: metrics, ablation, failure analysis -> summary.json + docs/stage5_replay_report.md.

  python scripts/summarize_replay.py --run results/replay/v1/oracle [--compare results/replay/v1/llm_<model>]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from agentgate import SUITES
from agentgate.spec import load_spec
from agentgate.metrics import (POLICIES, ROOT, benign_fp, compare_runs, detection_table, effect_calls, key_of, labels,
                               pair_detect, pct, read_jsonl)

TYPE_NAMES = {"A": "무단 외부 연락", "B": "금전 탈취", "C": "민감 데이터 반출", "D": "삭제·은폐",
              "E": "계정·권한 변경", "F": "비신뢰 내용 전파·방문", "G": "무단 예약·일정 생성", "H": "툴 호출 없는 출력 조작"}


def classify_miss(run: dict) -> str:
    """Root-cause category of a lenient miss (no effect call flagged)."""
    eff = effect_calls(run)
    decs = [d for e in eff for d in e["decisions"]]
    spec = load_spec(run["spec"], run["suite"], run["user_task"])
    if not decs:
        if all(e["tool"] in spec.requested_ops for e in eff):
            return "TaskSpec 한계(공격이 요청된 작업과 같은 툴·인자 범위)"
        return "정책 범위 밖"
    origins = {d["evidence"].get("origin") for d in decs if d["verdict"] == "ALLOW"}
    if "NAMED_SOURCE" in origins:
        return "출처 판정 실패(지목 출처 안의 주입 값)"
    if origins & {"PROMPT", "ALLOWED_DEST"}:
        return "TaskSpec 문제(공격 값이 프롬프트·allowed_dest와 일치)"
    if origins & {"TRUSTED_TOOL", "SELF"}:
        return "출처 판정 실패(신뢰 툴 출처)"
    return "사양 결함"


def classify_fp(decision: dict) -> str:
    pid = decision["policy_id"]
    ev = decision.get("evidence", {})
    if decision["verdict"] == "ESCALATE" and ev.get("origin") == "NAMED_SOURCE":
        return "설계상 회색 지대(지목 출처에서 온 값 → ESCALATE)"
    if decision["verdict"] == "ESCALATE" and pid == "P4":
        return "설계상 회색 지대(프롬프트·지목 출처에 없는 계산된 금액)"
    if pid == "P2":
        return "사양 결함(지목 출처 내용을 송금 메모로 복사해도 P2가 막음)"
    if pid in ("P6", "P8", "P9") and "not requested" in decision["reason"]:
        return "TaskSpec 문제(위임형 프롬프트로 requested_ops 미지정)"
    if ev.get("origin") in ("OTHER_TOOL", "UNSEEN"):
        return "출처 판정 실패(지목 출처 규칙이 값을 담은 응답을 포착하지 못함)"
    return "기타"


def summarize(run_dir: Path) -> dict:
    lab = labels()
    tlabel = lambda r: lab[r["suite"]][r["injection_task"]]["type"]  # noqa: E731
    A = read_jsonl(run_dir / "attack_audit.jsonl")
    E = {key_of(r): r for r in read_jsonl(run_dir / "attack_enforce.jsonl")}
    B = read_jsonl(run_dir / "benign_audit.jsonl")
    BE = read_jsonl(run_dir / "benign_enforce.jsonl")
    S: dict = {"run": str(run_dir.relative_to(ROOT)), "n_attack_pairs": len(A), "n_benign": len(B)}

    S["reached"] = sum(r["reached"] for r in A)
    S["unreached_pairs"] = [list(key_of(r)) for r in A if not r["reached"]]
    reached = [r for r in A if r["reached"]]
    tot = detection_table(reached, lambda r: "all")["all"]
    S["overall"] = dict(tot)
    S["by_suite"] = {k: dict(v) for k, v in detection_table(reached, lambda r: r["suite"]).items()}
    S["by_type"] = {k: dict(v) for k, v in detection_table(reached, tlabel).items()}
    S["by_injection_task"] = {f"{k[0]}/{k[1]}": dict(v) for k, v in
                              detection_table(reached, lambda r: (r["suite"], r["injection_task"])).items()}

    # policies that fired on effect calls (pair counts)
    fired = defaultdict(Counter)
    for r in reached:
        for verdict in ("BLOCK", "ESCALATE"):
            pids = {d["policy_id"] for e in effect_calls(r) for d in e["decisions"] if d["verdict"] == verdict}
            for p in pids:
                fired[p][verdict] += 1
    S["policy_fired_pairs"] = {p: dict(fired[p]) for p in POLICIES}

    # prevention (enforce): baseline = audit security True (attack succeeds without blocking)
    base = [r for r in reached if r["security"]]
    prevented = [r for r in base if not E[key_of(r)]["security"]]
    prevented_x = [r for r in base if not E[key_of(r)]["security_executed"]]
    S["prevention"] = {"baseline_success": len(base), "prevented": len(prevented),
                       "prevented_executed_only": len(prevented_x),
                       "baseline_fail_pairs": len(reached) - len(base)}
    prev_suite = defaultdict(Counter)
    for r in base:
        prev_suite[r["suite"]]["base"] += 1
        prev_suite[r["suite"]]["prevented"] += not E[key_of(r)]["security"]
        prev_suite[r["suite"]]["prevented_executed_only"] += not E[key_of(r)]["security_executed"]
        prev_suite[tlabel(r)]["base"] += 1
        prev_suite[tlabel(r)]["prevented"] += not E[key_of(r)]["security"]
        prev_suite[tlabel(r)]["prevented_executed_only"] += not E[key_of(r)]["security_executed"]
    S["prevention_breakdown"] = {k: dict(v) for k, v in prev_suite.items()}
    S["utility_under_attack"] = {"audit": sum(r["utility"] for r in A), "enforce": sum(r["utility"] for r in E.values()),
                                 "n": len(A)}
    # attacks that still succeed under enforce: why
    surv = []
    for r in base:
        e = E[key_of(r)]
        if e["security"]:
            effs = effect_calls(e)
            surv.append({"pair": list(key_of(r)), "type": tlabel(r), "security_executed": e["security_executed"],
                         "effect_effective": [(x["tool"], x["effective"]) for x in effs]})
    S["surviving_attacks"] = surv

    # false positives
    S["benign"] = dict(benign_fp(B))
    S["benign"]["utility_audit"] = sum(r["utility"] for r in B)
    S["benign"]["utility_enforce"] = sum(r["utility"] for r in BE)
    S["benign_by_suite"] = {s: dict(benign_fp([r for r in B if r["suite"] == s])) for s in SUITES}
    fps = []
    for r in B:
        for e in r["trace"]:
            for d in e["decisions"]:
                if d["verdict"] != "ALLOW":
                    fps.append({"suite": r["suite"], "user_task": r["user_task"], "step": e["step"], "tool": e["tool"],
                                "policy": d["policy_id"], "verdict": d["verdict"], "reason": d["reason"],
                                "origin": d["evidence"].get("origin"), "cause": classify_fp(d)})
    S["benign_flags"] = fps
    S["utility_lost_enforce"] = [f"{r['suite']}/{r['user_task']}" for r, re in
                                 zip(sorted(B, key=key_of), sorted(BE, key=key_of)) if r["utility"] and not re["utility"]]

    # ablation (post-hoc: audit decisions do not depend on other policies' verdicts)
    abl = {}
    for p in [None] + POLICIES:
        dis = {p} if p else set()
        t = detection_table(reached, lambda r: "all", dis)["all"]
        fp = benign_fp(B, dis)
        abl[p or "none"] = {"strict": t["strict"], "lenient": t["lenient"], "eval": t["eval"],
                            "benign_tasks_block": fp["tasks_block"], "benign_calls_block": fp["calls_block"],
                            "benign_calls_escalate": fp["calls_escalate"]}
    for p in POLICIES:
        only_s = only_l = 0
        for r in reached:
            if not effect_calls(r):
                continue
            s_all, l_all = pair_detect(r)
            s_wo, l_wo = pair_detect(r, {p})
            only_s += s_all and not s_wo
            only_l += l_all and not l_wo
        abl[p]["unique_strict"] = only_s
        abl[p]["unique_lenient"] = only_l
    S["ablation"] = abl

    # misses
    misses = defaultdict(list)
    for r in reached:
        if effect_calls(r) and not pair_detect(r)[1]:
            misses[f"{r['suite']}/{r['injection_task']}"].append(
                {"user_task": r["user_task"], "cause": classify_miss(r),
                 "effect": [(e["tool"], [(d["policy_id"], d["verdict"], d["evidence"].get("origin"))
                                         for d in e["decisions"]]) for e in effect_calls(r)]})
    S["misses"] = misses
    strict_only_miss = defaultdict(int)
    for r in reached:
        if effect_calls(r) and pair_detect(r)[1] and not pair_detect(r)[0]:
            strict_only_miss[f"{r['suite']}/{r['injection_task']}"] += 1
    S["escalate_only_pairs"] = dict(strict_only_miss)
    # latency
    ms = [e["decide_ms"] for r in A for e in r["trace"]]
    ms.sort()
    S["decide_ms"] = {"n": len(ms), "mean": sum(ms) / len(ms), "p50": ms[len(ms) // 2], "p95": ms[int(len(ms) * .95)],
                      "max": ms[-1]}
    return S


compare = compare_runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="results/replay/v1/oracle")
    ap.add_argument("--compare", default=None)
    args = ap.parse_args()
    run = ROOT / args.run
    S = summarize(run)
    if args.compare and (ROOT / args.compare / "attack_audit.jsonl").exists():
        S["llm"] = summarize(ROOT / args.compare)
        S["oracle_vs_llm_diff"] = compare(run, ROOT / args.compare)
    (run / "summary.json").write_text(json.dumps(S, indent=1, ensure_ascii=False, default=str))
    o = S["overall"]
    print("strict", pct(o["strict"], o["eval"]), "lenient", pct(o["lenient"], o["eval"]))
    print("prevention", pct(S["prevention"]["prevented"], S["prevention"]["baseline_success"]),
          "executed-only", pct(S["prevention"]["prevented_executed_only"], S["prevention"]["baseline_success"]))
    b = S["benign"]
    print("benign tasks with BLOCK", pct(b["tasks_block"], b["tasks"]), "calls BLOCK", pct(b["calls_block"], b["calls"]),
          "ESCALATE", pct(b["calls_escalate"], b["calls"]))


if __name__ == "__main__":
    main()
