"""native 어댑터(기준 구현): AgentDojo 자체 tool-calling pipeline을 harness Session 위에서 실행한다.

pipeline에는 `BridgeRuntime`을 넘긴다. 툴 목록은 session의 것이고, 모든 실행은 다른 프레임워크와 같이
`GateToolBridge.invoke` -> `Session.invoke` -> `PolicyGateRuntime.run_function`을 지난다. 시나리오 CLI와 대시보드도
native를 이 어댑터(`harness.runner`)로 돌린다.
"""

from __future__ import annotations

import time

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.errors import AbortAgentError
from agentdojo.functions_runtime import EmptyEnv, FunctionsRuntime
from agentdojo.task_suite.task_suite import model_output_from_messages
from agentdojo.types import get_text_content_as_str

from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.agents.factory import UsageMeter
from agentgate.scenarios.pipeline import _serialize_message, build_pipeline


def make_native_llm(model, meter: UsageMeter):
    """ModelSpec으로 AgentDojo pipeline의 LLM element를 만든다. 토큰·호출 수는 `meter`가 센다."""
    if model.provider == "anthropic":
        import anthropic

        from agentgate.agents.claude_agent import ClaudeAgentLLM
        meter.llm = ClaudeAgentLLM(anthropic.Anthropic(), model.model)
        return meter.llm
    if model.provider == "ollama" and not model.base_url:
        from agentgate.agents.ollama_agent import OllamaAgentLLM
        meter.llm = OllamaAgentLLM(model.model, temperature=model.temperature or 0.0)
        return meter.llm
    import openai
    from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
    kwargs = model.openai_kwargs()
    client = meter.wrap_openai(openai.OpenAI(base_url=kwargs["base_url"], api_key=kwargs["api_key"]))
    llm = OpenAILLM(client, model.model, temperature=model.temperature)
    llm.name = model.model
    return llm


class _EventingLLM(BasePipelineElement):
    """감싼 LLM element 앞뒤로 llm_query / llm_response 이벤트를 낸다(응답 시간은 `duration_seconds`)."""

    def __init__(self, inner, sink):
        self.inner, self.sink, self.name, self.step = inner, sink, getattr(inner, "name", "llm"), 0

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        self.step += 1
        self.sink.emit("llm_query", f"{self.name} · {len(messages)} messages", step=self.step,
                       data={"model": self.name, "messages": len(messages)})
        started = time.perf_counter()
        try:
            result = self.inner.query(query, runtime, env, messages, extra_args)
        except Exception as error:
            self.sink.emit("llm_error", f"{type(error).__name__}", level="ERROR", step=self.step,
                           data={"error_type": type(error).__name__,
                                 "duration_seconds": round(time.perf_counter() - started, 3)})
            raise
        message = result[3][-1]
        calls = [{tc.function: dict(tc.args)} for tc in message.get("tool_calls") or []]
        self.sink.emit("llm_response", ", ".join(next(iter(c)) for c in calls) or "answer", step=self.step,
                       data={"actions": calls, "content": get_text_content_as_str(message.get("content") or [])[:1500],
                             "duration_seconds": round(time.perf_counter() - started, 3)})
        return result


class BridgeRuntime(FunctionsRuntime):
    """AgentDojo runtime for the pipeline: lists the session's tools, executes them only through the bridge."""

    def __init__(self, bridge):
        super().__init__([])
        self.bridge = bridge
        self.functions = bridge.session.runtime.functions

    def run_function(self, env, function, kwargs, raise_on_error=False):
        text, record = self.bridge.invoke_detail(function, dict(kwargs))
        if record.get("blocked"):   # same (output, error) shape as GatedRuntime for a blocked call
            return text, text
        if record.get("error") is not None:
            return "", text
        return text, None


class NativeAdapter(FrameworkAdapter):
    name = "native"
    display_name = "AgentDojo native"
    description = "AgentDojo tool-calling pipeline (reference)"
    package = "agentdojo"
    routes_tools_through_bridge = True

    def run(self, task, bridge, model, options, max_steps, sink):
        session = bridge.session
        meter = UsageMeter()
        llm = make_native_llm(model, meter)
        pipeline = build_pipeline(_EventingLLM(llm, sink), None)
        runtime = BridgeRuntime(bridge)
        messages, model_output = [], None
        for _ in range(3):   # same retry rule as run_live_task
            try:
                _, _, session.env, messages, _ = pipeline.query(task, runtime, session.env)
            except AbortAgentError as error:
                session.env, messages = error.task_environment, error.messages
            model_output = model_output_from_messages(messages) if messages else None
            if model_output is not None:
                break
        meter.collect()
        return FrameworkResult(
            final_output=get_text_content_as_str(model_output or []),
            steps=sum(1 for m in messages if m["role"] == "assistant"),
            usage={"input": meter.input_tokens, "output": meter.output_tokens, "calls": meter.calls,
                   "refusals": meter.refusals},
            messages=[_serialize_message(m) for m in messages])
