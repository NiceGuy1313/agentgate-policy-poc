"""Stage 4 task 4: extract TaskSpecs for all 97 user tasks with the LLM extractor.

  python scripts/extract_specs.py [--suites banking] [--workers 4]
Needs AGENTGATE_LLM_PROVIDER / AGENTGATE_LLM_MODEL and the provider key in .env.
Writes data/specs/llm/<model>/<suite>/<task>.yaml and data/specs/llm/<model>/extraction_log.json.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.llm.client import LLMClient
from agentgate.llm.extractor import TaskSpecExtractor, ToolInfo
from agentgate.suites import load_suite, tool_infos

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="*", default=list(SUITES))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    try:
        client = LLMClient()
    except RuntimeError as e:
        raise SystemExit(f"not run: {e}")
    ex = TaskSpecExtractor(client)
    model = client.model.replace("/", "_")
    out_root = ROOT / "data" / "specs" / "llm" / model
    jobs = []
    for s in a.suites:
        suite = load_suite(s)
        tools = [ToolInfo(**t) for t in tool_infos(suite)]
        for tid, ut in suite.user_tasks.items():
            jobs.append((s, tid, ut.PROMPT, tools))

    def run(job):
        s, tid, prompt, tools = job
        r = ex.extract(prompt, tools)
        if r.spec is not None:
            r.spec.status = "llm"
            r.spec.dump(out_root / s / f"{tid}.yaml")
        return {"suite": s, "task_id": tid, "ok": r.spec is not None, "cached": r.cached, "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "attempts": r.attempts,
                "error": r.error}

    with ThreadPoolExecutor(a.workers) as pool:
        log = list(pool.map(run, jobs))
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "extraction_log.json").write_text(json.dumps({"model": client.model, "runs": log}, indent=1))
    ok = sum(x["ok"] for x in log)
    print(f"{ok}/{len(log)} extracted with {client.model}; failures: {[x['task_id'] for x in log if not x['ok']]}")


if __name__ == "__main__":
    main()
