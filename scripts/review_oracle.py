"""Stage 4 tasks 1 (review table) and 2 (GT cross-check) -> docs/stage4_oracle_review.md.

Task 2 runs only after the drafts exist: it opens the ground truth and lists, without changing any spec,
every state-changing / outbound ground-truth call the oracle spec would not allow.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentgate import SUITES
from agentgate import normalize as N
from agentgate.config import effect_tools, policies_config
from agentgate.spec import TaskSpec

ROOT = Path(__file__).resolve().parents[1]
OPS_POLICED = set(policies_config()["P6"]["tools"]) | set(policies_config()["P8"]["tools"]) | \
    set(policies_config()["P9"]["tools"])


def _p2_blocked() -> set[tuple[str, str, str]]:
    """(suite, task, tool) where the benign oracle replay got a P2 BLOCK."""
    path = ROOT / "results/replay/v1/oracle/benign_audit.jsonl"
    out = set()
    if path.exists():
        for line in path.open():
            r = json.loads(line)
            for e in r["trace"]:
                if any(d["policy_id"] == "P2" and d["verdict"] == "BLOCK" for d in e["decisions"]):
                    out.add((r["suite"], r["user_task"], e["tool"]))
    return out


P2_BLOCKED = _p2_blocked()


def gt_mismatches(spec: TaskSpec, prompt: str, gt: list[dict], key=("", "")) -> list[str]:
    out = []
    eff = effect_tools() - {"get_webpage"}
    p1 = policies_config()["P1"]["targets"]
    ph = N.norm_haystack(prompt)
    allowed = N.norm_haystack("\n".join(spec.allowed_dest))
    for c in gt:
        t = c["function"]
        if t in eff and t not in spec.requested_ops:
            tag = " (P6/P8/P9가 BLOCK)" if t in OPS_POLICED else ""
            out.append(f"requested_ops 누락: `{t}`{tag}")
        for arg in p1.get(t, []):
            for v in N.iter_values(c["args"].get(arg)):
                if not N.contains(ph, v) and not N.contains(allowed, v):
                    out.append(f"allowed_dest 밖 목적지: `{t}.{arg}={v}` (P1이 출처로 판정)")
        if t in ("send_money", "schedule_transaction") and spec.amount_cap is not None:
            amt = N.parse_amount(c["args"].get("amount"))
            if amt is not None and amt > spec.amount_cap:
                out.append(f"amount_cap 초과: `{t}.amount={amt}` > {spec.amount_cap}")
        if (key[0], key[1], t) in P2_BLOCKED and not spec.egress_requested:
            out.append(f"egress_requested=false 인데 `{t}` 인자가 민감 읽기 결과와 겹쳐 P2가 BLOCK (정상 replay 기준)")
    return sorted(set(out))


def main():
    users = {(u["suite"], u["task_id"]): u for u in json.loads((ROOT / "data/catalog/user_tasks.json").read_text())}
    rows, mism = [], []
    for s in SUITES:
        for f in sorted((ROOT / "data/specs/oracle" / s).glob("*.yaml"), key=lambda p: int(p.stem.split("_")[-1])):
            spec = TaskSpec.load(f)
            u = users[(s, f.stem)]
            amb = (spec.notes or "").startswith("[ambiguous]")
            rows.append((amb, s, f.stem, " ".join(u["PROMPT"].split())[:90], spec))
            for m in gt_mismatches(spec, u["PROMPT"], u["ground_truth"], (s, f.stem)):
                mism.append((s, f.stem, m))
    rows.sort(key=lambda r: (not r[0],))
    n_amb = sum(r[0] for r in rows)
    status = sorted({r[4].status for r in rows})
    L = ["# Stage 4 — oracle TaskSpec 검토표", "",
         "`scripts/review_oracle.py`가 `data/specs/oracle/`과 `data/catalog/user_tasks.json`에서 생성한다.", "",
         "## 작성 원칙", "",
         "- 근거는 user task의 PROMPT 텍스트와 suite 툴 목록(이름·설명)뿐이다. 초안 작성(`scripts/seed_oracle_specs.py`) 시 ground truth, injection GOAL, 환경 데이터를 참고하지 않았다.",
         "- 프롬프트가 함의하는 상태 변경·외부 전송 툴은 `requested_ops`에 넣었다. 판단이 갈리는 항목은 `notes`에 `[ambiguous]`로 표시했다.",
         "- 작업이 데이터 원천(메일·파일·웹페이지)에 위임된 프롬프트(\"파일에 적힌 대로 하라\")는 프롬프트만으로 `requested_ops`를 정할 수 없으므로 비워 두었다. 이는 사람이 검토해 결정할 항목이다.",
         "", f"- 상태: {', '.join(status)} (사람 검토 후 `status: reviewed`로 바꾼다)",
         f"- 애매함 표시: {n_amb}건 / {len(rows)}건", f"- GT 대조 불일치: {len(mism)}건", "",
         "## 검토표 (애매함 표시 항목 먼저)", "",
         "| 애매 | suite | task | PROMPT 요약 | requested_ops | named_sources | allowed_dest | amount_cap | egress | notes |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for amb, s, t, p, sp in rows:
        L.append(f"| {'●' if amb else ''} | {s} | {t.replace('user_task_', 'u')} | {p.replace('|', '/')} | "
                 f"{', '.join(sp.requested_ops)} | {', '.join(sp.named_sources)} | {', '.join(sp.allowed_dest)} | "
                 f"{'' if sp.amount_cap is None else sp.amount_cap} | {'T' if sp.egress_requested else 'F'} | "
                 f"{(sp.notes or '').replace('[ambiguous] ', '').replace('|', '/')} |")
    L += ["", "## GT 대조 불일치", "",
          "ground truth의 상태 변경·외부 전송 호출 중 oracle spec이 허용하지 않는 항목이다. spec은 수정하지 않았다. "
          "수정 여부는 \"프롬프트를 읽은 사람도 그렇게 판단했을 것인가\"를 기준으로 사람이 결정한다. "
          "`allowed_dest 밖 목적지`는 P1이 출처(NAMED_SOURCE/OTHER_TOOL/TRUSTED_TOOL)로 판정하므로 그 자체로 오류는 아니다.", "",
          "| suite | task | 불일치 |", "|---|---|---|"]
    for s, t, m in mism:
        L.append(f"| {s} | {t.replace('user_task_', 'u')} | {m} |")
    (ROOT / "docs" / "stage4_oracle_review.md").write_text("\n".join(L) + "\n")
    print(f"ambiguous={n_amb} gt_mismatches={len(mism)}")


if __name__ == "__main__":
    main()
