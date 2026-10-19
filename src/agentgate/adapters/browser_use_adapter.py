"""browser-use (NiceGuy1313 fork with boundary logs): AgentDojo tools as browser-use custom actions.

The browser actions are excluded (AgentDojo tasks never need the web); only `done` stays. Each AgentDojo tool is
registered as an action whose function calls `GateToolBridge.invoke`, so the fork's `Registry.execute_action`
boundary log wraps the gate decision: tool_start -> gate_decision -> tool_end. The Agent still needs a
BrowserSession; it runs a headless Chrome on about:blank. Step results come from the `on_step_end` hook.

browser-use pins `anthropic`/`openai` versions that conflict with agentgate's, so it runs in its own venv
(`python:` in config/frameworks.yaml; see README).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agentgate.adapters.base import FrameworkAdapter, FrameworkResult
from agentgate.harness.events import preview
from agentgate.harness.tool_schema import arguments

DASHBOARD_DIR = Path(__file__).resolve().parents[3] / "dashboard"
JSON_TO_PYTHON = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
# browser-use's own scratch file system (a temp dir, not the AgentDojo environment). Its system prompt tells the model
# to plan in todo.md with these actions, so they stay available; without them models keep calling write_file.
FILE_ACTIONS = ("write_file", "replace_file", "read_file")
# An AgentDojo tool that takes the name of a file-system action also accepts that action's argument name
# (banking read_file(file_path) vs browser-use read_file(file_name)).
ARG_ALIASES = {("read_file", "file_path"): "file_name"}


def param_model(spec: dict):
    """pydantic ActionModel for one AgentDojo tool, built from its flattened JSON schema."""
    from typing import Literal, Optional, Union

    from browser_use.tools.registry.views import ActionModel
    from pydantic import AliasChoices, Field, create_model

    fields = {}
    for arg in arguments(spec["parameters"]):
        if arg.enum:
            kind = Literal[tuple(arg.enum)]
        elif arg.types == ["array"]:
            kind = list[JSON_TO_PYTHON.get((arg.items or {}).get("type"), str)]
        else:
            kinds = [JSON_TO_PYTHON.get(t, str) for t in arg.types]
            kind = kinds[0] if len(kinds) == 1 else Union[tuple(kinds)]
        if arg.nullable:
            kind = Optional[kind]
        default = ... if arg.required else None
        alias = ARG_ALIASES.get((spec["name"], arg.name))
        fields[arg.name] = (kind, Field(default, description=arg.description,
                                        validation_alias=AliasChoices(arg.name, alias) if alias else None))
    return create_model(f"{spec['name']}_params", __base__=ActionModel, **fields)


def make_tools(bridge):
    from browser_use import Tools

    defaults = list(Tools().registry.registry.actions)
    tools = Tools(exclude_actions=[name for name in defaults if name != "done" and name not in FILE_ACTIONS])
    for spec in bridge.tool_specs():
        name = spec["name"]
        if name in tools.registry.exclude_actions:   # an AgentDojo tool named like a browser action
            tools.registry.exclude_actions.remove(name)
        # an AgentDojo tool named like a file-system action replaces it (registered below under the same name)

        tools.registry.action(spec["description"] or name, param_model=param_model(spec))(_gated_action(bridge, name))
    return tools


def _gated_action(bridge, name: str):
    async def action(params):
        return bridge.invoke(name, {k: v for k, v in params.model_dump().items() if v is not None})

    action.__name__ = name
    return action


def embedded_object(text: str) -> dict | None:
    """The first JSON object in a reply, also inside prose or a code fence and with raw newlines in strings, or None."""
    start = text.find("{")
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder(strict=False).raw_decode(text, start)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def lenient_output(output_format):
    """`output_format` whose model_validate_json also accepts JSON surrounded by prose, or prose alone.

    Ollama does not enforce `response_format` for gpt-oss, which sometimes writes
    'We will issue read_file ... {"thinking": ..., "action": [...]}' or a ```json fence with raw newlines in strings
    (validation is retried on the embedded object, parsed leniently)
    or answers in prose only ('I'm sorry, but I can't help with that.'), which becomes a `done` action with that
    text, as smolagents and mini-swe-agent treat a prose reply as the final answer. The result is still an
    `output_format` instance; an empty reply or broken JSON fails as before.
    """
    from pydantic import ValidationError

    def model_validate_json(cls, data, **kwargs):
        try:
            return output_format.model_validate_json(data, **kwargs)
        except ValidationError:
            text = (data.decode() if isinstance(data, bytes) else str(data)).strip()
            inner = embedded_object(text)
            if inner is not None:
                return output_format.model_validate(inner)
            if not text or "{" in text:
                raise
            return output_format.model_validate({"action": [{"done": {"text": text}}]})

    return type(output_format.__name__, (output_format,), {"model_validate_json": classmethod(model_validate_json)})


def make_llm(model):
    """browser-use chat model for a ModelSpec (structured output through the provider's JSON-schema mode)."""
    if model.provider == "anthropic":
        from browser_use import ChatAnthropic
        return ChatAnthropic(model=model.model, api_key=model.api_key())
    from browser_use import ChatOpenAI

    class LenientChatOpenAI(ChatOpenAI):
        async def ainvoke(self, messages, output_format=None, **kwargs):
            return await super().ainvoke(messages, lenient_output(output_format) if output_format else None, **kwargs)

    return LenientChatOpenAI(**model.openai_kwargs(), temperature=model.temperature, frequency_penalty=None)


class BrowserUseAdapter(FrameworkAdapter):
    name = "browser-use"
    display_name = "browser-use"
    description = "browser-use Agent with AgentDojo tools as custom actions (headless Chrome), NiceGuy1313 fork"
    requires = ("browser_use",)
    package = "browser-use"
    loggers = ("browser_use",)
    internal_tools = frozenset({"done", *FILE_ACTIONS})
    routes_tools_through_bridge = True   # every AgentDojo action calls bridge.invoke

    def options_schema(self) -> dict:
        return {"type": "object", "properties": {
            "max_actions_per_step": {"type": "integer", "title": "Max actions per step", "default": 5}}}

    def architecture_html(self) -> Path | None:
        path = DASHBOARD_DIR / "static" / "arch" / "browser-use.html"
        return path if path.exists() else None

    def run(self, task, bridge, model, options, max_steps, sink):
        os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        import asyncio

        from browser_use import Agent, BrowserProfile, BrowserSession

        async def on_step_end(agent) -> None:
            item = agent.history.history[-1] if agent.history.history else None
            if item is None:
                return
            brain = item.model_output
            errors = [r.error for r in item.result if r.error]
            done = any(r.is_done for r in item.result)
            thought = " · ".join(x for x in (brain.thinking, brain.evaluation_previous_goal, brain.next_goal) if x) \
                if brain else ""
            sink.emit("step", "error" if errors else ("final answer" if done else "ok"),
                      level="ERROR" if errors else "INFO", step=item.metadata.step_number if item.metadata else None,
                      data={"type": "action", "model_output": preview(thought), "error": "; ".join(errors) or None,
                            "is_final_answer": done, "memory": preview(brain.memory) if brain else None,
                            "results": [{"error": r.error, "content": preview(r.extracted_content, 600)}
                                        for r in item.result],
                            "duration_seconds": round(item.metadata.duration_seconds, 3) if item.metadata else None})

        async def main():
            session = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=False))
            agent = Agent(task=task, llm=make_llm(model), tools=make_tools(bridge), browser_session=session,
                          use_vision=False, use_judge=False, directly_open_url=False, enable_planning=False,
                          max_actions_per_step=int(options.get("max_actions_per_step") or 5))
            try:
                return agent, await agent.run(max_steps=max_steps, on_step_end=on_step_end)
            finally:
                await session.kill()

        agent, history = asyncio.run(main())
        usage = history.usage
        steps = history.number_of_steps()
        return FrameworkResult(
            final_output=history.final_result(), steps=steps,
            usage={"input": usage.total_prompt_tokens if usage else 0,
                   "output": usage.total_completion_tokens if usage else 0, "calls": steps},
            info={"success": history.is_successful(), "errors": [e for e in history.errors() if e]})
