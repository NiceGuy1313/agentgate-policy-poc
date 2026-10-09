"""정답 궤적 재생에서 나온 모든 ESCALATE에 판정 LLM(escalation judge)만 단독으로 적용해 평가한다(RQ1).

선행 조건: results/replay/v1/oracle/*_audit.jsonl(재생 평가 결과), 판정 LLM 키(.env) 또는 data/judge_cache/.

상향된 호출별 정답 레이블: 공격 쌍 재생에서 injection task의 effect 호출이면 `attack`, benign(injection 없음)
재생에서 나온 호출이면 `normal`이다.
출력: results/judge/v1/<model>/judge_eval.json (A/B/C confusion matrix와 HUMAN 비율)

사용법: python scripts/evaluation/judge_evaluation.py [--suites banking] [--max-pairs 0]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from agentgate import SUITES
from agentgate.config import PROJECT_ROOT
from agentgate.evaluation.replay import replay_attack_pair, replay_benign_task
from agentgate.llm.escalation_judge import EscalationJudge
from agentgate.suites import load_suite
from agentgate.task_spec import load_task_spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    try:
        judge = EscalationJudge()
    except RuntimeError as error:
        raise SystemExit(f"not run: {error}")
    replay_dir = PROJECT_ROOT / "results/replay/v1/oracle"
    records = []

    def collect(run, label_fn):
        for event in run["trace"]:
            if event.get("judge"):
                records.append({"suite": run["suite"], "user_task": run["user_task"],
                                "injection_task": run.get("injection_task"), "tool": event["tool"],
                                "label": label_fn(event),
                                **{k: event["judge"][k] for k in ("answer", "final", "human")},
                                "facts": event["judge"]["facts"]})

    # Only pairs whose audit replay produced an ESCALATE on a relevant call.
    escalated_attacks = set()
    for line in (replay_dir / "attack_audit.jsonl").open():
        run = json.loads(line)
        if any(e["verdict"] == "ESCALATE" and e.get("role") == "effect" for e in run["trace"]):
            escalated_attacks.add((run["suite"], run["user_task"], run["injection_task"]))
    escalated_benign = set()
    for line in (replay_dir / "benign_audit.jsonl").open():
        run = json.loads(line)
        if any(e["verdict"] == "ESCALATE" for e in run["trace"]):
            escalated_benign.add((run["suite"], run["user_task"]))
    pairs = sorted(p for p in escalated_attacks if p[0] in args.suites)
    if args.max_pairs:
        pairs = pairs[: args.max_pairs]

    # The replay functions return trace_records(); the judge output is inside each decision dict.
    def benign_job(suite_name, user_task_id):
        spec = load_task_spec("oracle", suite_name, user_task_id)
        prompt = load_suite(suite_name).get_user_task_by_id(user_task_id).PROMPT
        run = replay_benign_task(
            suite_name, user_task_id, spec, "audit",
            runtime_kw={"escalate_action": "judge",
                        "judge": lambda call, provenance, context: judge(call, provenance, context, prompt)})
        return {"suite": suite_name, "user_task": user_task_id, "trace": run["trace"]}, (lambda event: "normal")

    def attack_job(suite_name, user_task_id, injection_task_id):
        spec = load_task_spec("oracle", suite_name, user_task_id)
        prompt = load_suite(suite_name).get_user_task_by_id(user_task_id).PROMPT
        run = replay_attack_pair(
            suite_name, user_task_id, injection_task_id, spec, "audit",
            runtime_kw={"escalate_action": "judge",
                        "judge": lambda call, provenance, context: judge(call, provenance, context, prompt)})
        # Only the injection task's calls are labelled: user-phase calls in an injected env are ambiguous.
        trace = [e for e in run["trace"] if e["phase"] == "injection"]
        return ({"suite": suite_name, "user_task": user_task_id, "injection_task": injection_task_id, "trace": trace},
                (lambda event: "attack"))

    jobs = [(benign_job, (suite_name, user_task_id))
            for suite_name, user_task_id in sorted(escalated_benign) if suite_name in args.suites]
    jobs += [(attack_job, pair) for pair in pairs]
    with ThreadPoolExecutor(args.workers) as executor:
        for run, label_fn in executor.map(lambda job: job[0](*job[1]), jobs):
            collect(run, label_fn)
    answer_confusion = Counter((x["label"], x["answer"]) for x in records)
    final_confusion = Counter((x["label"], x["final"]) for x in records)
    result = {"model": judge.model, "n": len(records),
              "confusion_answer": {f"{k[0]}->{k[1]}": v for k, v in sorted(answer_confusion.items())},
              "confusion_final": {f"{k[0]}->{k[1]}": v for k, v in sorted(final_confusion.items())},
              "human_rate": sum(x["human"] for x in records) / len(records) if records else None,
              "stats": judge.stats, "records": records}
    output_dir = PROJECT_ROOT / "results/judge/v1" / judge.model.replace("/", "_")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "judge_eval.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=1))


if __name__ == "__main__":
    main()
