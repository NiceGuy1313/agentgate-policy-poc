"""정답(ground truth) 궤적을 재생하는 결정론적 scripted 에이전트이다. API 키 없이 쓴다."""

from __future__ import annotations

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv
from agentdojo.types import ChatAssistantMessage, get_text_content_as_str, text_content_block_from_string

from agentgate.scenarios.attack_trajectories import plan_attack_calls


class ScriptedAgent(BasePipelineElement):
    """Deterministic stand-in for an LLM agent (no API key needed).

    Executes the user task's ground truth one call per turn. As soon as a tool result contains the
    injection marker it is "hijacked": it inserts the injection task's reference trajectory, then
    resumes the user task. After a policy block it continues with its plan (`on_block="continue"`) or
    stops (`"stop"`). Used to exercise the real AgentDojo pipeline + gate end to end; it measures
    integration and enforcement, not how a real model reacts to injections or blocks.
    """

    name = "scripted-agent (gpt-4o-2024-05-13 persona for attack templating)"

    def __init__(self, suite_name: str, user_task, injection_task=None, on_block: str = "continue",
                 marker: str = "<INFORMATION>"):
        self.suite_name, self.user_task, self.injection_task = suite_name, user_task, injection_task
        self.on_block, self.marker = on_block, marker
        self.reset()

    def reset(self):
        self.plan: list | None = None
        self.hijacked = False
        self.stopped = False

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        if self.plan is None:
            self.plan = list(self.user_task.ground_truth(env.model_copy(deep=True)))
        last_message = messages[-1] if messages else None
        if last_message is not None and last_message["role"] == "tool":
            text = get_text_content_as_str(last_message["content"]) + (last_message.get("error") or "")
            if "Tool call blocked by" in text and self.on_block == "stop":
                self.stopped = True
            if (self.injection_task is not None and not self.hijacked and self.marker in text):
                self.hijacked = True
                injection_calls, _ = plan_attack_calls(self.suite_name, self.injection_task, env)
                self.plan = list(injection_calls) + self.plan
        if self.plan and not self.stopped:
            call = self.plan.pop(0)
            message = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string("")],
                                           tool_calls=[call])
        else:
            outputs = [self.user_task.GROUND_TRUTH_OUTPUT]
            if self.hijacked and self.injection_task is not None:
                outputs.append(self.injection_task.GROUND_TRUTH_OUTPUT)
            message = ChatAssistantMessage(role="assistant",
                                           content=[text_content_block_from_string("\n".join(o for o in outputs if o))],
                                           tool_calls=None)
        return query, runtime, env, [*messages, message], extra_args
