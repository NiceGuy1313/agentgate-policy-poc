"""실험 조건(no_gate, gate_llm_judge, gate_static_rules)의 런타임 조립. ToolSpec 추출기는 suite마다 하나를
스레드 사이에 공유한다."""

from __future__ import annotations

import threading

from agentgate.policy_gate.runtime import PolicyGateRuntime

_EXTRACTORS: dict = {}
_LOCK = threading.Lock()


def toolspec_extractor(suite_name: str):
    from agentgate.policy_gate.toolspec import ToolSpecExtractor
    from agentgate.scenarios.suites import describe_tools, load_suite
    with _LOCK:
        if suite_name not in _EXTRACTORS:
            _EXTRACTORS[suite_name] = ToolSpecExtractor(describe_tools(load_suite(suite_name)))
        return _EXTRACTORS[suite_name]


def build(cond, *, suite_name: str, prompt: str, escalate: str, stats=None):
    if cond.judge is None:   # no_gate: record calls only, no ToolSpec extraction, no judge
        return PolicyGateRuntime, {}
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

    kwargs = {"toolspec": toolspec}
    if escalate == "judge" and cond.judge == "rules":
        from agentgate.policy_gate.rule_checker import RuleChecker
        kwargs["judge"] = RuleChecker()
    elif escalate == "judge":
        from agentgate.policy_gate.llm_judge import LLMJudge, as_runtime_judge
        judge = LLMJudge(use_rules=cond.judge == "llm")
        kwargs["judge"] = as_runtime_judge(judge, prompt)
    return PolicyGateRuntime, kwargs
