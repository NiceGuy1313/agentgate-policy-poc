"""oracle TaskSpec 검토표와 ground truth 교차 검사 결과를 data/agentdojo/oracle_specs/review.md에 쓴다.

교차 검사는 초안이 만들어진 뒤에만 수행한다. ground truth를 열어, spec을 고치지 않고 oracle spec이 허용하지
않는 상태 변경·외부 전송 ground-truth 호출을 모두 나열한다.

사용법: python scripts/taskspec/review_oracle_specs.py
"""

from __future__ import annotations

import json

from agentgate import SUITES, v1
from agentgate.config import PROJECT_ROOT, load_effect_tools
from agentgate.gate.normalization import build_haystack, haystack_contains, iterate_leaf_values, parse_amount
from agentgate.gate.task_spec import TaskSpec

OPS_POLICED = set(v1.load_config()["P6"]["tools"]) | set(v1.load_config()["P8"]["tools"]) | \
    set(v1.load_config()["P9"]["tools"])


def _p2_blocked_calls() -> set[tuple[str, str, str]]:
    """(suite, task, tool) where the benign oracle replay got a P2 BLOCK."""
    path = PROJECT_ROOT / "results/replay/v1/oracle/benign_audit.jsonl"
    blocked = set()
    if path.exists():
        for line in path.open():
            record = json.loads(line)
            for event in record["trace"]:
                if any(d["policy_id"] == "P2" and d["verdict"] == "BLOCK" for d in event["decisions"]):
                    blocked.add((record["suite"], record["user_task"], event["tool"]))
    return blocked


P2_BLOCKED = _p2_blocked_calls()


def gt_mismatches(spec: TaskSpec, prompt: str, ground_truth: list[dict], key=("", "")) -> list[str]:
    mismatches = []
    effect_tools = load_effect_tools() - {"get_webpage"}
    p1_targets = v1.load_config()["P1"]["targets"]
    prompt_haystack = build_haystack(prompt)
    allowed_haystack = build_haystack("\n".join(spec.allowed_dest))
    for call in ground_truth:
        tool = call["function"]
        if tool in effect_tools and tool not in spec.requested_ops:
            tag = " (P6/P8/P9가 BLOCK)" if tool in OPS_POLICED else ""
            mismatches.append(f"requested_ops 누락: `{tool}`{tag}")
        for arg in p1_targets.get(tool, []):
            for value in iterate_leaf_values(call["args"].get(arg)):
                if not haystack_contains(prompt_haystack, value) and not haystack_contains(allowed_haystack, value):
                    mismatches.append(f"allowed_dest 밖 목적지: `{tool}.{arg}={value}` (P1이 출처로 판정)")
        if tool in ("send_money", "schedule_transaction") and spec.amount_cap is not None:
            amount = parse_amount(call["args"].get("amount"))
            if amount is not None and amount > spec.amount_cap:
                mismatches.append(f"amount_cap 초과: `{tool}.amount={amount}` > {spec.amount_cap}")
        if (key[0], key[1], tool) in P2_BLOCKED and not spec.egress_requested:
            mismatches.append(f"egress_requested=false 인데 `{tool}` 인자가 민감 읽기 결과와 겹쳐 P2가 BLOCK "
                              "(정상 replay 기준)")
    return sorted(set(mismatches))


def main():
    user_tasks = {(u["suite"], u["task_id"]): u
                  for u in json.loads((PROJECT_ROOT / "data/agentdojo/benchmark/user_tasks.json").read_text())}
    rows, mismatches = [], []
    for suite in SUITES:
        spec_paths = sorted((PROJECT_ROOT / "data/agentdojo/oracle_specs" / suite).glob("*.yaml"),
                            key=lambda p: int(p.stem.split("_")[-1]))
        for spec_path in spec_paths:
            spec = TaskSpec.load(spec_path)
            user_task = user_tasks[(suite, spec_path.stem)]
            ambiguous = (spec.notes or "").startswith("[ambiguous]")
            rows.append((ambiguous, suite, spec_path.stem, " ".join(user_task["PROMPT"].split())[:90], spec))
            for mismatch in gt_mismatches(spec, user_task["PROMPT"], user_task["ground_truth"],
                                          (suite, spec_path.stem)):
                mismatches.append((suite, spec_path.stem, mismatch))
    rows.sort(key=lambda r: (not r[0],))
    ambiguous_count = sum(r[0] for r in rows)
    status = sorted({r[4].status for r in rows})
    lines = ["# oracle TaskSpec 검토표", "",
             "`scripts/taskspec/review_oracle_specs.py`가 `data/agentdojo/oracle_specs/`과 `data/agentdojo/benchmark/user_tasks.json`에서 생성한다.", "",
             "## 작성 원칙", "",
             "- 근거는 user task의 PROMPT 텍스트와 suite 툴 목록(이름·설명)뿐이다. 초안 작성(`scripts/taskspec/seed_oracle_specs.py`) 시 ground truth, injection GOAL, 환경 데이터를 참고하지 않았다.",
             "- 프롬프트가 함의하는 상태 변경·외부 전송 툴은 `requested_ops`에 넣었다. 판단이 갈리는 항목은 `notes`에 `[ambiguous]`로 표시했다.",
             "- 작업이 데이터 원천(메일·파일·웹페이지)에 위임된 프롬프트(\"파일에 적힌 대로 하라\")는 프롬프트만으로 `requested_ops`를 정할 수 없으므로 비워 두었다. 이는 사람이 검토해 결정할 항목이다.",
             "", f"- 상태: {', '.join(status)} (사람 검토 후 `status: reviewed`로 바꾼다)",
             f"- 애매함 표시: {ambiguous_count}건 / {len(rows)}건", f"- GT 대조 불일치: {len(mismatches)}건", "",
             "## 검토표 (애매함 표시 항목 먼저)", "",
             "| 애매 | suite | task | PROMPT 요약 | requested_ops | named_sources | allowed_dest | amount_cap | egress | notes |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for ambiguous, suite, task, prompt_summary, spec in rows:
        lines.append(
            f"| {'●' if ambiguous else ''} | {suite} | {task.replace('user_task_', 'u')} | "
            f"{prompt_summary.replace('|', '/')} | "
            f"{', '.join(spec.requested_ops)} | {', '.join(spec.named_sources)} | {', '.join(spec.allowed_dest)} | "
            f"{'' if spec.amount_cap is None else spec.amount_cap} | {'T' if spec.egress_requested else 'F'} | "
            f"{(spec.notes or '').replace('[ambiguous] ', '').replace('|', '/')} |")
    lines += ["", "## GT 대조 불일치", "",
              "ground truth의 상태 변경·외부 전송 호출 중 oracle spec이 허용하지 않는 항목이다. spec은 수정하지 않았다. "
              "수정 여부는 \"프롬프트를 읽은 사람도 그렇게 판단했을 것인가\"를 기준으로 사람이 결정한다. "
              "`allowed_dest 밖 목적지`는 P1이 출처(NAMED_SOURCE/OTHER_TOOL/TRUSTED_TOOL)로 판정하므로 그 자체로 오류는 아니다.", "",
              "| suite | task | 불일치 |", "|---|---|---|"]
    for suite, task, mismatch in mismatches:
        lines.append(f"| {suite} | {task.replace('user_task_', 'u')} | {mismatch} |")
    (PROJECT_ROOT / "data" / "agentdojo" / "oracle_specs" / "review.md").write_text("\n".join(lines) + "\n")
    print(f"ambiguous={ambiguous_count} gt_mismatches={len(mismatches)}")


if __name__ == "__main__":
    main()
