"""smolagents (NiceGuy1313 fork with boundary logs): AgentDojo tools as smolagents `Tool`s.

Every tool's `forward` calls `GateToolBridge.invoke`, so the fork's `Tool.__call__` boundary log
(`[TOOL_EXECUTION_START]` ...) wraps the gate decision: tool_start -> gate_decision -> tool_end.
Step results come from `step_callbacks`, as in the fork's dashboard (examples/agent_dashboard/app.py).
"""

from __future__ import annotations

from pathlib import Path

from agentgate.agents.frameworks.base import FrameworkAdapter, FrameworkResult
from agentgate.harness.events import preview
from agentgate.harness.tool_schema import arguments

DASHBOARD_DIR = Path(__file__).resolve().parents[4] / "dashboard"


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
            args = {k: v for k, v in kwargs.items() if v is not None}
            return self._bridge.invoke(self.name, args)

        cls = type(f"AgentDojo_{spec['name']}", (Tool,), {
            "name": spec["name"], "description": spec["description"] or spec["name"], "inputs": inputs,
            "output_type": "string", "skip_forward_signature_validation": True, "forward": forward})
        tool = cls()
        tool._bridge = bridge
        tools.append(tool)
    return tools


class SmolagentsAdapter(FrameworkAdapter):
    name = "smolagents"
    display_name = "smolagents"
    description = "HF smolagents (ToolCallingAgent / CodeAgent), NiceGuy1313 fork"
    requires = ("smolagents", "litellm")
    package = "smolagents"
    loggers = ("smolagents",)
    internal_tools = frozenset({"final_answer"})

    def options_schema(self) -> dict:
        return {"type": "object", "properties": {
            "agent_type": {"type": "string", "title": "Agent type", "enum": ["tool_calling", "code"],
                           "default": "tool_calling"}}}

    def architecture_html(self) -> Path | None:
        path = DASHBOARD_DIR / "static" / "arch" / "smolagents.html"
        return path if path.exists() else None

    def run(self, task, bridge, model, options, max_steps, sink):
        from smolagents import CodeAgent, LiteLLMModel, ToolCallingAgent
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
        llm = LiteLLMModel(**kwargs)
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
