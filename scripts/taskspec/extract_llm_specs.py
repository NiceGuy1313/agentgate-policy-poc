"""LLM 추출기로 user task 97개 전체의 TaskSpec을 추출한다.

사용법: python scripts/taskspec/extract_llm_specs.py [--suites banking] [--workers 4]

.env에 AGENTGATE_LLM_PROVIDER / AGENTGATE_LLM_MODEL과 해당 provider 키가 필요하다.
출력: data/specs/llm/<model>/<suite>/<task>.yaml, data/specs/llm/<model>/extraction_log.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from agentgate import SUITES
from agentgate.config import PROJECT_ROOT
from agentgate.llm.client import LLMClient
from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
from agentgate.suites import describe_tools, load_suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    try:
        client = LLMClient()
    except RuntimeError as error:
        raise SystemExit(f"not run: {error}")
    extractor = TaskSpecExtractor(client)
    model = client.model.replace("/", "_")
    output_root = PROJECT_ROOT / "data" / "specs" / "llm" / model
    jobs = []
    for suite_name in args.suites:
        suite = load_suite(suite_name)
        tools = [ToolInfo(**tool) for tool in describe_tools(suite)]
        for task_id, user_task in suite.user_tasks.items():
            jobs.append((suite_name, task_id, user_task.PROMPT, tools))

    def run(job):
        suite_name, task_id, prompt, tools = job
        result = extractor.extract(prompt, tools)
        if result.spec is not None:
            result.spec.status = "llm"
            result.spec.dump(output_root / suite_name / f"{task_id}.yaml")
        return {"suite": suite_name, "task_id": task_id, "ok": result.spec is not None, "cached": result.cached,
                "latency_ms": result.latency_ms, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens, "attempts": result.attempts, "error": result.error}

    with ThreadPoolExecutor(args.workers) as pool:
        log = list(pool.map(run, jobs))
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "extraction_log.json").write_text(json.dumps({"model": client.model, "runs": log}, indent=1))
    extracted = sum(entry["ok"] for entry in log)
    failures = [entry["task_id"] for entry in log if not entry["ok"]]
    print(f"{extracted}/{len(log)} extracted with {client.model}; failures: {failures}")


if __name__ == "__main__":
    main()
