"""적합성 검사(conformance kit): 프레임워크 어댑터나 Wrapper가 모든 툴 호출을 Gate에 올바르게 통과시키는지 확인한다.

LLM·네트워크 없이 돈다. ToolSpec 추출기는 빈 것으로 바꾸고(`offline_toolspec`), 각 프레임워크는 실제 모델 경로로
대본형 가짜 LLM(`fake_llm.FakeLLMServer`)에 붙여 구동한다. 기준은 `ScriptedAgent`를 쓴 native AgentDojo
pipeline(`run_live_task`)이다. 쌍·조건마다 다음을 요구한다.
  - 어댑터가 의무 Gate를 선언한다(`FrameworkAdapter.contract_problems`가 비어 있음).
  - Gate trace(툴, 최종 판정, 차단 여부)와 utility·security가 기준과 같다.
  - 차단된 호출이 실행되지 않고 차단 전후 환경이 같다(check_enforcement.check_run).
  - 유효한 run이다: Gate 밖 툴 실행이 없다(경계 로그 감사, 런타임이 거부한 실행). tool_start가 기록되면 이벤트 순서가
    tool_start -> gate_decision -> tool_end이다(mini-swe-agent `bash` 같은 프레임워크 내부 dispatcher 안의 판정은 예외).

  python -m agentgate.harness.conformance --framework smolagents --condition gate_static_rules
  python -m agentgate.harness.conformance --entry "python path/to/wrapper.py"     # 등록하지 않은 Wrapper
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from contextlib import contextmanager
from unittest import mock

from agentgate.harness import registry
from agentgate.harness.events import EventSink
from agentgate.harness.fake_llm import FakeLLMServer, ScriptedPlan
from agentgate.harness.models import ModelSpec
from agentgate.harness.runner import RunRequest, run_scenario
from agentgate.harness.session import CONDITIONS, ScenarioConfig, Session
from agentgate.policy_gate.toolspec import ToolSpecResult

DEFAULT_PAIRS = [("banking", "user_task_0", "injection_task_0"), ("banking", "user_task_3", "injection_task_4"),
                 ("slack", "user_task_1", "injection_task_1"), ("banking", "user_task_2", None)]
DEFAULT_CONDITIONS = ("no_gate", "gate_static_rules")


class OfflineToolSpec:
    """ToolSpec extractor stand-in: every tool output gets an empty ToolSpec (no LLM call)."""

    def extract(self, tool: str, args: dict, output_text: str) -> ToolSpecResult:
        return ToolSpecResult({"records": [], "requests": []}, "offline", True)


@contextmanager
def offline_toolspec():
    with mock.patch("agentgate.policy_gate.assembly.toolspec_extractor", lambda suite_name: OfflineToolSpec()):
        yield


def _signature(trace: list[dict]) -> list[tuple]:
    return [(e["tool"], e["effective"], e["blocked"]) for e in trace]


def reference(pair, condition: str) -> dict:
    from agentgate.agents.scripted_agent import ScriptedAgent
    from agentgate.scenarios.pipeline import RecordingPipeline, build_pipeline, run_live_task

    session = Session(ScenarioConfig(*pair, condition=condition, attack_model_name="GPT-4"))
    llm = ScriptedAgent(pair[0], session.user_task, session.injection_task)
    result = run_live_task(session.suite, RecordingPipeline(build_pipeline(llm, None)), session.user_task,
                           session.injection_task, session.injections, session.runtime)
    return {**result, "trace": session.runtime.trace_records()}


def _event_order_issues(events, internal_tools: frozenset[str] = frozenset()) -> list[str]:
    """A gate decision must sit inside its own tool's tool_start, or inside a framework-internal dispatcher
    (mini-swe-agent's `bash`, whose commands are the gated tool calls)."""
    issues, open_tools = [], []
    for event in events:
        if event.kind == "tool_start":
            open_tools.append(event.data.get("action"))
        elif event.kind == "gate_decision" and not event.data.get("decide_only") and open_tools:
            if open_tools[-1] != event.data.get("tool") and open_tools[-1] not in internal_tools:
                issues.append(f"gate_decision for {event.data.get('tool')} inside tool_start {open_tools[-1]}")
        elif event.kind in ("tool_end", "tool_error") and open_tools:
            open_tools.pop()
    return issues


def run_case(framework: str, pair, condition: str, options: dict | None = None) -> dict:
    with offline_toolspec():
        return _run_case(framework, pair, condition, options)


def _run_case(framework: str, pair, condition: str, options: dict | None = None) -> dict:
    sys.path.insert(0, str(registry.CONFIG_DIR.parent / "scripts" / "analysis"))
    from check_enforcement import check_run

    contract = [f"adapter contract: {p}" for p in registry.get(framework).contract_problems()]
    if contract:   # the runner would refuse it; nothing to run
        return {"framework": framework, "pair": ":".join(p or "none" for p in pair), "condition": condition,
                "ok": False, "problems": contract, "trace": []}
    expected = reference(pair, condition)
    config = ScenarioConfig(*pair, condition=condition, attack_model_name="GPT-4")
    session = Session(config)
    sink = EventSink()
    with FakeLLMServer(ScriptedPlan(session)) as server:
        request = RunRequest(framework, ModelSpec("openai_compat", "scripted", base_url=server.base_url), config,
                             options or {})
        record = run_scenario(request, sink, session)
    problems = []
    if record.get("valid") is False:
        problems.append(f"invalid run: {record.get('invalid_reason')}")
    if record.get("error"):
        problems.append(f"run error: {record['error'][:300]}")
    if _signature(record["trace"]) != _signature(expected["trace"]):
        problems.append(f"trace differs: got {_signature(record['trace'])}, expected {_signature(expected['trace'])}")
    for key in ("utility", "security", "security_executed"):
        if record.get(key) != expected.get(key):
            problems.append(f"{key}: got {record.get(key)}, expected {expected.get(key)}")
    problems += [f"enforcement: {issue}" for issue in check_run(record)]
    problems += [f"ungated: {issue}" for issue in record.get("ungated_calls", [])]
    problems += _event_order_issues(sink.events, registry.get(framework).internal_tools)
    return {"framework": framework, "pair": ":".join(p or "none" for p in pair), "condition": condition,
            "ok": not problems, "problems": problems, "trace": _signature(record["trace"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--framework", help="registered framework name")
    group.add_argument("--entry", help="command of an unregistered contract v1 Wrapper")
    parser.add_argument("--conditions", "--condition", dest="conditions", nargs="+", default=list(DEFAULT_CONDITIONS),
                        choices=list(CONDITIONS))
    parser.add_argument("--options", default="{}", help="framework options as JSON")
    args = parser.parse_args()
    name = args.framework
    if args.entry:
        name = "conformance-entry"
        registry._entries()[name] = {"contract": {"command": shlex.split(args.entry)}}
    if reason := registry.get(name).unavailable_reason():
        print(f"UNAVAILABLE {name}: {reason}")
        sys.exit(2)
    results = [run_case(name, pair, condition, json.loads(args.options))
               for pair in DEFAULT_PAIRS for condition in args.conditions]
    for result in results:
        print(("PASS" if result["ok"] else "FAIL"), result["condition"], result["pair"],
              "" if result["ok"] else "\n    " + "\n    ".join(result["problems"]))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} passed")
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
