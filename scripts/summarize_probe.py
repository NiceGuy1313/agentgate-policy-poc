"""Per-model table for a probe run (scripts/probe_models.sh): did the model act on the injection?

  python scripts/summarize_probe.py --run results/live/v1/probe_tool_knowledge
Writes <run>/probe_summary.json and prints a markdown table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from summarize_live import _catalog, attack_attempted, attack_call_steps  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    run = ROOT / a.run
    users, injs = _catalog()
    rows = []
    for d in sorted(p for p in run.iterdir() if p.is_dir()):
        files = sorted(d.glob("C*/*/*/*.json"))
        rs = [json.loads(f.read_text()) for f in files]
        ok = [r for r in rs if not r.get("error")]
        att = [r for r in ok if attack_attempted(r, users, injs)]
        pairs = []
        for r in ok:
            # gate verdicts on the attack calls only (calls that act on the injection)
            gate = sorted({r["trace"][i]["verdict"] for i in attack_call_steps(r, users, injs)
                           if r["trace"][i].get("verdict") != "ALLOW"})
            pairs.append({"pair": f"{r['suite']}/{r['user_task']}/{r['injection_task']}",
                          "attempted": attack_attempted(r, users, injs), "attack_success": r.get("security"),
                          "utility": r.get("utility"), "refusals": r["agent_tokens"].get("refusals", 0),
                          "gate_would": gate, "calls": [e["tool"] for e in r["trace"]]})
        rows.append({"model": next((r.get("agent_model") for r in rs), d.name), "runs": len(rs),
                     "errors": len(rs) - len(ok), "attempted": len(att), "attack_success": sum(bool(r.get("security")) for r in ok),
                     "utility": sum(bool(r.get("utility")) for r in ok),
                     "error_example": next((r["error"][:160] for r in rs if r.get("error")), None), "pairs": pairs})
    (run / "probe_summary.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print("| model | runs | errors | attempted | attack success (ASR) | user task utility |")
    print("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (-x["attempted"], x["model"])):
        print(f"| {r['model']} | {r['runs']} | {r['errors']} | {r['attempted']} | {r['attack_success']} | {r['utility']} |")
    for r in rows:
        if r["error_example"]:
            print(f"- {r['model']} error: {r['error_example']}")


if __name__ == "__main__":
    main()
