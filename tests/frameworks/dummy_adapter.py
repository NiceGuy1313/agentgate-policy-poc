"""A minimal third-party-style adapter used by the registry and conformance tests."""

from agentgate.adapters.base import FrameworkAdapter, FrameworkResult


class DummyAdapter(FrameworkAdapter):
    """Calls the tools the scripted fake LLM asks for, through the bridge, with a plain OpenAI client loop."""

    name = "dummy"
    display_name = "Dummy"

    def run(self, task, bridge, model, options, max_steps, sink):
        import json

        import openai

        client = openai.OpenAI(**{k: v for k, v in model.openai_kwargs().items() if k != "model"})
        tools = [{"type": "function", "function": spec} for spec in bridge.tool_specs()]
        messages = [{"role": "user", "content": task}]
        for _ in range(max_steps):
            reply = client.chat.completions.create(model=model.model, messages=messages, tools=tools).choices[0]
            calls = reply.message.tool_calls or []
            if not calls:
                return FrameworkResult(final_output=reply.message.content)
            messages.append(reply.message.model_dump(exclude_none=True))
            for call in calls:
                output = bridge.invoke(call.function.name, json.loads(call.function.arguments or "{}"))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        return FrameworkResult(final_output=None, error="max steps")
