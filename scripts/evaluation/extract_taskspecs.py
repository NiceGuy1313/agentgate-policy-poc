"""97 user task의 TaskSpec을 LLM으로 추출해 data/specs/llm/<name>/<suite>/<task>.yaml로 저장한다(재생 평가용).

입력은 사용자 프롬프트와 툴 목록뿐이다. --cache-dir를 live 실행의 --spec-cache-dir와 같게 주면 재생과 live가
같은 추출 결과를 쓴다.
사용법: python scripts/evaluation/extract_taskspecs.py --name claude-sonnet-5-5-v3 --cache-dir data/specs/llm_cache_v3
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import SUITES
from agentgate.config import PROJECT_ROOT
from agentgate.llm.taskspec_extractor import TaskSpecExtractor, ToolInfo
from agentgate.suites import describe_tools, load_suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    extractor = TaskSpecExtractor(cache_dir=PROJECT_ROOT / args.cache_dir)
    jobs = []
    for suite_name in SUITES:
        suite = load_suite(suite_name)
        tools = [ToolInfo(**t) for t in describe_tools(suite)]
        jobs += [(suite_name, task_id, task.PROMPT, tools) for task_id, task in suite.user_tasks.items()]

    def run(job):
        suite_name, task_id, prompt, tools = job
        result = extractor.extract(prompt, tools)
        if result.spec is not None:
            result.spec.dump(PROJECT_ROOT / "data" / "specs" / "llm" / args.name / suite_name / f"{task_id}.yaml")
        return result

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, jobs))
    print(len(results), "specs;", sum(r.spec is None for r in results), "failed;",
          sum(bool(r.spec and r.spec.delegated_sources) for r in results), "with delegated_sources")


if __name__ == "__main__":
    main()
