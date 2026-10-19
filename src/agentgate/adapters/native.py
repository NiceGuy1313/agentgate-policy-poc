"""Reference adapter: AgentDojo's own tool-calling pipeline, on a harness Session.

The pipeline gets a `BridgeRuntime`: the session's tool list, but every execution goes through `GateToolBridge.invoke`
(-> `Session.invoke` -> `PolicyGateRuntime.run_function`), like any other framework.
"""

from __future__ import annotations

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.errors import AbortAgentError
from agentdojo.functions_runtime import EmptyEnv, FunctionsRuntime
from agentdojo.task_suite.task_suite import model_output_from_messages
from agentdojo.types import get_text_content_as_str

from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.scenarios.pipeline import _serialize_message, build_pipeline


def make_native_llm(model):
    """ModelSpec -> AgentDojo pipeline element (same classes as agentgate.agents.factory.make_llm)."""
    if model.provider == "anthropic":
        import anthropic

        from agentgate.agents.claude_agent import ClaudeAgentLLM
        return ClaudeAgentLLM(anthropic.Anthropic(), model.model)
    if model.provider == "ollama" and not model.base_url:
        from agentgate.agents.ollama_agent import OllamaAgentLLM
        return OllamaAgentLLM(model.model, temperature=model.temperature or 0.0)
    import openai
    from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
    kwargs = model.openai_kwargs()
    llm = OpenAILLM(openai.OpenAI(base_url=kwargs["base_url"], api_key=kwargs["api_key"]), model.model,
                    temperature=model.temperature)
    llm.name = model.model
    return llm


class _EventingLLM(BasePipelineElement):
    """Publishes llm_query / llm_response around the wrapped LLM element."""

    def __init__(self, inner, sink):
        self.inner, self.sink, self.name, self.step = inner, sink, getattr(inner, "name", "llm"), 0

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        self.step += 1
        self.sink.emit("llm_query", f"{self.name} · {len(messages)} messages", step=self.step,
                       data={"model": self.name, "messages": len(messages)})
        try:
            result = self.inner.query(query, runtime, env, messages, extra_args)
        except Exception as error:
            self.sink.emit("llm_error", f"{type(error).__name__}", level="ERROR", step=self.step,
                           data={"error_type": type(error).__name__})
            raise
        message = result[3][-1]
        calls = [{tc.function: dict(tc.args)} for tc in message.get("tool_calls") or []]
        self.sink.emit("llm_response", ", ".join(next(iter(c)) for c in calls) or "answer", step=self.step,
                       data={"actions": calls, "content": get_text_content_as_str(message.get("content") or [])[:1500]})
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
        llm = make_native_llm(model)
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
        usage = getattr(llm, "usage", None) or {}
        return FrameworkResult(
            final_output=get_text_content_as_str(model_output or []),
            steps=sum(1 for m in messages if m["role"] == "assistant"),
            usage={"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                   "calls": usage.get("calls", 0)},
            messages=[_serialize_message(m) for m in messages])
