"""smolagents 어댑터(NiceGuy1313 fork, 경계 로그 포함): AgentDojo 툴을 smolagents `Tool`로 등록한다.

각 툴의 `forward`가 `GateToolBridge.invoke`를 부르므로 fork의 `Tool.__call__` 경계 로그(`[TOOL_EXECUTION_START]` …)가
tool_start -> gate_decision -> tool_end 순서로 Gate 판정을 감싼다. 단계 결과는 `step_callbacks`에서 얻는다.
새 어댑터를 만들 때 참조 구현으로 쓴다(docs/integration_guide.md).
"""

from __future__ import annotations

import json
import uuid

from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.harness.events import preview
from agentgate.harness.tool_schema import arguments, gated_callable


def make_tools(bridge):
    from smolagents import Tool

    tools = []
    for spec in bridge.tool_specs():
        inputs = {}
        for arg in arguments(spec["parameters"]):
            entry = {"type": arg.types[0] if len(arg.types) == 1 else arg.types, "description": arg.description}
            if arg.nullable:
                entry["nullable"] = True
            if arg.items is not None:
                entry["items"] = arg.items
            if arg.enum is not None:
                entry["enum"] = arg.enum
            inputs[arg.name] = entry

        def forward(self, **kwargs):
            return self._invoke(kwargs)

        cls = type(f"AgentDojo_{spec['name']}", (Tool,), {
            "name": spec["name"], "description": spec["description"] or spec["name"], "inputs": inputs,
            "output_type": "string", "skip_forward_signature_validation": True, "forward": forward})
        tool = cls()
        tool._invoke = gated_callable(bridge, spec["name"])
        tools.append(tool)
    return tools


def _text_of(content) -> str:
    if isinstance(content, list):
        return "".join(part.get("text") or "" for part in content if isinstance(part, dict))
    return content or ""


def recover_tool_call(text: str, tools) -> tuple[str, dict] | None:
    """(tool name, arguments) for a reply that carries a tool call as plain content, or None.

    gpt-oss on Ollama writes the final answer on the harmony `final` channel, so the reply has no
    `tool_calls` and `tool_choice="required"` is not enforced. Observed content shapes:
    `{"name": ..., "arguments": ...}` (smolagents parses it itself), arguments only (`{"answer": ...}`),
    and plain prose. Arguments only map to the single tool whose required inputs they cover; prose
    becomes `final_answer`. Anything else is left to smolagents, which reports a parsing error.
    """
    text = text.strip()
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1], strict=False)
        except json.JSONDecodeError:
            return None
    if data is None:
        names = {tool.name for tool in tools}
        return ("final_answer", {"answer": text}) if "final_answer" in names else None
    if not isinstance(data, dict) or not data or "name" in data:
        return None
    matches = [tool.name for tool in tools
               if set(data) <= set(tool.inputs)
               and {k for k, v in tool.inputs.items() if not v.get("nullable")} <= set(data)]
    return (matches[0], data) if len(matches) == 1 else None


def make_model(**kwargs):
    """LiteLLMModel that never sends an assistant message without content and tool calls.

    When a model returns an empty reply, smolagents keeps it in memory as an empty assistant message; some
    OpenAI-compatible endpoints (mistral on Ollama Cloud) reject the next request with a 400 for that.
    A reply whose tool call is in the content instead of `tool_calls` goes through `recover_tool_call`.
    """
    from smolagents import LiteLLMModel
    from smolagents.models import ChatMessageToolCall, ChatMessageToolCallFunction, parse_json_if_needed

    class SafeLiteLLMModel(LiteLLMModel):
        _tools: list = []

        def _prepare_completion_kwargs(self, *args, **inner):
            self._tools = list(inner.get("tools_to_call_from") or [])
            completion_kwargs = super()._prepare_completion_kwargs(*args, **inner)
            for message in completion_kwargs.get("messages") or []:
                if (getattr(message.get("role"), "value", message.get("role")) == "assistant" and not message.get("tool_calls")
                        and not _text_of(message.get("content")).strip()):
                    message["content"] = "(empty response)"
            return completion_kwargs

        def generate(self, *args, **inner):
            return self._drop_unknown_arguments(super().generate(*args, **inner))

        def parse_tool_calls(self, message):
            if not message.tool_calls:
                recovered = recover_tool_call(_text_of(message.content), self._tools)
                if recovered:
                    message.tool_calls = [ChatMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}", type="function",
                        function=ChatMessageToolCallFunction(name=recovered[0], arguments=recovered[1]))]
            return self._drop_unknown_arguments(super().parse_tool_calls(message))

        def _drop_unknown_arguments(self, message):
            # gpt-oss sends {"n": null}, {"n": {}} or {"n": 10} to get_scheduled_transactions, which takes no
            # arguments. smolagents fails the step on any key outside the schema; AgentDojo's runtime (pydantic,
            # extra="ignore") ignores such keys, so they are dropped here to give the same behaviour.
            inputs = {tool.name: tool.inputs for tool in self._tools}
            for call in message.tool_calls or []:
                arguments = parse_json_if_needed(call.function.arguments)
                if isinstance(arguments, dict) and call.function.name in inputs:
                    call.function.arguments = {k: v for k, v in arguments.items()
                                               if k in inputs[call.function.name]}
            return message

    return SafeLiteLLMModel(**kwargs)


class SmolagentsAdapter(FrameworkAdapter):
    name = "smolagents"
    display_name = "smolagents"
    description = "HF smolagents (ToolCallingAgent / CodeAgent), NiceGuy1313 fork"
    requires = ("smolagents", "litellm")
    package = "smolagents"
    loggers = ("smolagents",)
    internal_tools = frozenset({"final_answer"})
    routes_tools_through_bridge = True   # every tool's forward / command calls bridge.invoke

    def options_schema(self) -> dict:
        return {"type": "object", "properties": {
            "agent_type": {"type": "string", "title": "Agent type", "enum": ["tool_calling", "code"],
                           "default": "tool_calling"}}}

    def run(self, task, bridge, model, options, max_steps, sink):
        from smolagents import CodeAgent, ToolCallingAgent
        from smolagents.memory import ActionStep, PlanningStep

        def on_action(step: ActionStep) -> None:
            error = str(step.error) if step.error else None
            usage = step.token_usage
            sink.emit("step", "error" if error else ("final answer" if step.is_final_answer else "ok"),
                      level="ERROR" if error else "INFO", step=step.step_number,
                      data={"type": "action", "model_output": preview(step.model_output),
                            "code": step.code_action, "observations": preview(step.observations),
                            "error": error, "is_final_answer": step.is_final_answer,
                            "output": preview(step.action_output, 600),
                            "duration_seconds": round(step.timing.duration, 3) if step.timing.duration else None,
                            "input_tokens": usage.input_tokens if usage else None,
                            "output_tokens": usage.output_tokens if usage else None})

        def on_plan(step: PlanningStep) -> None:
            sink.emit("step", "plan", step=sink.current_step, data={"type": "planning", "plan": preview(step.plan)})

        kwargs = model.litellm_kwargs()
        llm = make_model(**kwargs)
        agent_cls = CodeAgent if options.get("agent_type") == "code" else ToolCallingAgent
        agent = agent_cls(tools=make_tools(bridge), model=llm, max_steps=max_steps, verbosity_level=-1,
                          step_callbacks={ActionStep: on_action, PlanningStep: on_plan})
        result = agent.run(task, return_full_result=True)
        usage = result.token_usage
        steps = sum(isinstance(s, ActionStep) for s in agent.memory.steps)
        return FrameworkResult(
            final_output=None if result.output is None else str(result.output), steps=steps,
            usage={"input": usage.input_tokens if usage else 0, "output": usage.output_tokens if usage else 0,
                   "calls": steps},
            info={"state": result.state})
