"""v3 실험 조건(C6, C7, C7n) 런타임 조립. ToolSpec 추출기는 suite마다 하나를 스레드 사이에 공유한다."""

from __future__ import annotations

import threading

from agentgate import v3
from agentgate.v3.runtime import ToolSpecGatedRuntime

_EXTRACTORS: dict = {}
_LOCK = threading.Lock()


def toolspec_extractor(suite_name: str):
    from agentgate.agentdojo.suites import describe_tools, load_suite
    from agentgate.v3.toolspec import ToolSpecExtractor
    with _LOCK:
        if suite_name not in _EXTRACTORS:
            _EXTRACTORS[suite_name] = ToolSpecExtractor(describe_tools(load_suite(suite_name)))
        return _EXTRACTORS[suite_name]


def build(cond, *, suite_name: str, prompt: str, escalate: str, disabled=None, stats=None):
    extractor = toolspec_extractor(suite_name)

    def toolspec(tool, args, text):
        result = extractor.extract(tool, args, text)
        if stats is not None:
            toolspec_stats = stats.setdefault("toolspec", {"calls": 0, "api_calls": 0, "errors": 0, "input_tokens": 0,
                                                           "output_tokens": 0, "latency_ms": 0.0})
            toolspec_stats["calls"] += 1
            toolspec_stats["api_calls"] += not result.cached and result.error is None
            toolspec_stats["errors"] += result.error is not None
            toolspec_stats["input_tokens"] += 0 if result.cached else result.input_tokens
            toolspec_stats["output_tokens"] += 0 if result.cached else result.output_tokens
            toolspec_stats["latency_ms"] += 0 if result.cached else result.latency_ms
        return result.spec

    kwargs = {"engine": v3.make_engine(disabled, cond.gate), "toolspec": toolspec}
    if escalate == "judge":
        from agentgate.v3.judge import TrustedJudgeV3, as_runtime_judge
        judge = TrustedJudgeV3(gate=cond.gate, use_rules=cond.judge != "v3-norules")
        kwargs["judge"] = as_runtime_judge(judge, prompt)
    return ToolSpecGatedRuntime, kwargs
