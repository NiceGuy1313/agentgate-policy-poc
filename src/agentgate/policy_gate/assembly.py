"""ToolSpec 부착 함수 조립(`agentgate.experiment.conditions.make_runtime`이 쓴다). ToolSpec 추출기는 suite마다 하나를
스레드 사이에 공유한다. 테스트·오프라인 검사는 `toolspec_extractor`를 바꿔 끼운다."""

from __future__ import annotations

import threading

_EXTRACTORS: dict = {}
_LOCK = threading.Lock()


def toolspec_extractor(suite_name: str):
    from agentgate.policy_gate.toolspec import ToolSpecExtractor
    from agentgate.scenarios.suites import describe_tools, load_suite
    with _LOCK:
        if suite_name not in _EXTRACTORS:
            _EXTRACTORS[suite_name] = ToolSpecExtractor(describe_tools(load_suite(suite_name)))
        return _EXTRACTORS[suite_name]


def toolspec_labeller(suite_name: str, stats: dict | None = None):
    """(tool, args, output_text) -> ToolSpec dict | None for `PolicyGateRuntime(toolspec=...)`; `stats` collects
    the extractor's LLM usage."""
    extractor = toolspec_extractor(suite_name)

    def toolspec(tool, args, text):
        result = extractor.extract(tool, args, text)
        if stats is not None:
            toolspec_stats = stats.setdefault("toolspec", {"calls": 0, "api_calls": 0, "errors": 0, "input_tokens": 0,
                                                           "output_tokens": 0, "latency_ms": 0.0,
                                                           "cache_read_input_tokens": 0,
                                                           "cache_creation_input_tokens": 0})
            toolspec_stats["calls"] += 1
            toolspec_stats["api_calls"] += not result.cached and result.error is None
            toolspec_stats["errors"] += result.error is not None
            toolspec_stats["input_tokens"] += 0 if result.cached else result.input_tokens
            toolspec_stats["output_tokens"] += 0 if result.cached else result.output_tokens
            toolspec_stats["latency_ms"] += 0 if result.cached else result.latency_ms
            toolspec_stats["cache_read_input_tokens"] += result.cache_read_input_tokens
            toolspec_stats["cache_creation_input_tokens"] += result.cache_creation_input_tokens
        return result.spec

    return toolspec
