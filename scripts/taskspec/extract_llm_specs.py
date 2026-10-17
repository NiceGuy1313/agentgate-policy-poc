"""LLM 추출기로 user task 97개 전체의 TaskSpec을 추출해 재생 평가용으로 저장한다.

입력은 사용자 프롬프트와 툴 목록뿐이다. 추출 프롬프트와 캐시는 버전이 정한다(v1·v2: prompts/v1, v3: prompts/v3).
캐시(data/<version>/cache/taskspec/<model>/)를 live 실행과 같이 쓰므로, 재생과 live가 같은 추출 결과를 쓴다.

사용법: python scripts/taskspec/extract_llm_specs.py --version v3 [--suites banking] [--workers 4]
.env: AGENTGATE_LLM_MODEL(기본 claude-sonnet-5-5)과 provider 키(캐시에 없을 때만 호출)
출력: data/<version>/specs/<model>/<suite>/<task>.yaml, extraction_log.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from agentgate import SUITES
from agentgate.agentdojo.suites import describe_tools, load_suite
from agentgate.gate.task_spec import llm_spec_dir, taskspec_version
from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["v1", "v3"], default="v1", help="TaskSpec prompt version (v2 uses v1)")
    parser.add_argument("--model", default=None, help="default AGENTGATE_LLM_MODEL")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    extractor = TaskSpecExtractor(model=args.model, version=taskspec_version(args.version))
    output_root = llm_spec_dir(extractor.model, args.version)
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
    (output_root / "extraction_log.json").write_text(json.dumps(
        {"model": extractor.model, "version": args.version, "prompt": str(extractor.template_path.name), "runs": log},
        indent=1))
    extracted = sum(entry["ok"] for entry in log)
    failures = [entry["task_id"] for entry in log if not entry["ok"]]
    print(f"{extracted}/{len(log)} extracted with {extractor.model} ({args.version} prompt); failures: {failures}")


if __name__ == "__main__":
    main()
