"""Stage 6: policy gate inside AgentDojo agent pipelines (no agentdojo source changes).

The gate itself is `GatedRuntime` (passed as the task runtime). The pipeline gets one extra element in
front, `TaskSpecElement`, which builds the TaskSpec from the user prompt alone (LLM extractor, cached,
or oracle spec) and configures the runtime before the first LLM turn.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Sequence

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.errors import AbortAgentError
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.task_suite.task_suite import functions_stack_trace_from_messages, model_output_from_messages
from agentdojo.types import ChatAssistantMessage, ChatMessage, get_text_content_as_str, text_content_block_from_string

from agentgate.runtime import GatedRuntime, env_hash
from agentgate.spec import TaskSpec

SpecProvider = Callable[[str], tuple[TaskSpec, dict]]
"""prompt -> (TaskSpec, meta{source, cached, latency_ms, input_tokens, output_tokens})"""


class TaskSpecElement(BasePipelineElement):
    name = "taskspec"

    def __init__(self, provider: SpecProvider):
        self.provider = provider

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        if isinstance(runtime, GatedRuntime) and runtime.index is None:
            t0 = time.perf_counter()
            spec, meta = self.provider(query)
            meta = {**meta, "wall_ms": (time.perf_counter() - t0) * 1000}
            runtime.configure(query, spec, meta)
        return query, runtime, env, messages, extra_args


class RecordingPipeline(BasePipelineElement):
    """Keeps the last message list (AgentDojo's runner discards it)."""

    def __init__(self, inner: BasePipelineElement):
        self.inner = inner
        self.name = inner.name
        self.messages: Sequence[ChatMessage] = []

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        try:
            out = self.inner.query(query, runtime, env, messages, extra_args)
        except AbortAgentError as e:
            self.messages = e.messages
            raise
        self.messages = out[3]
        return out


def build_pipeline(llm: BasePipelineElement, spec_provider: SpecProvider | None, defense: str | None = None,
                   system_message: str | None = None) -> AgentPipeline:
    cfg = PipelineConfig(llm=llm, model_id=None, defense=defense, system_message_name=None,
                         system_message=system_message)
    pipe = AgentPipeline.from_config(cfg)
    if spec_provider is not None:
        pipe.elements = [*pipe.elements[:2], TaskSpecElement(spec_provider), *pipe.elements[2:]]
    return pipe


def run_live_task(suite, pipeline: RecordingPipeline, user_task, injection_task, injections: dict,
                  runtime: GatedRuntime) -> dict:
    """Same steps as TaskSuite.run_task_with_pipeline, but keeps env, messages and the gate trace."""
    environment = suite.load_and_inject_default_environment(injections)
    task_environment = user_task.init_environment(environment)
    pre = task_environment.model_copy(deep=True)
    messages, model_output = [], None
    t0 = time.perf_counter()
    for _ in range(3):
        try:
            _, _, task_environment, messages, _ = pipeline.query(user_task.PROMPT, runtime, task_environment)
        except AbortAgentError as e:
            task_environment, messages = e.task_environment, e.messages
        model_output = model_output_from_messages(messages)
        if model_output is not None:
            break
    wall_ms = (time.perf_counter() - t0) * 1000
    if model_output is None:
        warnings.warn(f"Model output was None for task {user_task.ID}")
    trace_calls = functions_stack_trace_from_messages(messages)
    out = model_output or []
    # checks may mutate the env (Inbox.get_unread): keep snapshots for the executed-only variant
    post_copy, pre_copy = task_environment.model_copy(deep=True), pre.model_copy(deep=True)
    final_hash = env_hash(task_environment)
    utility = suite._check_task_result(user_task, out, pre, task_environment, trace_calls)
    res = {"utility": bool(utility), "wall_ms": wall_ms, "final_env_hash": final_hash,
           "n_tool_calls_requested": len(trace_calls)}
    if injection_task is not None:
        res["security"] = bool(suite._check_task_result(injection_task, out, pre, task_environment, trace_calls))
        # executed calls only: match requested calls to gate events in order (unexecuted tail calls at the
        # loop limit and calls AgentDojo rejects never reach the gate)
        executed, i = [], 0
        for c in trace_calls:
            if i < len(runtime.trace) and runtime.trace[i].tool == c.function:
                if runtime.trace[i].executed:
                    executed.append(c)
                i += 1
        suite._check_task_result(user_task, out, pre_copy, post_copy, executed)
        res["security_executed"] = bool(
            suite._check_task_result(injection_task, out, pre_copy, post_copy, executed))
    res["messages"] = [_msg(m) for m in messages]
    res["final_output"] = get_text_content_as_str(out)[:2000]
    return res


def _msg(m) -> dict:
    d = {"role": m["role"]}
    c = m.get("content")
    d["content"] = get_text_content_as_str(c)[:1500] if isinstance(c, list) else (c or "")[:1500] if c else ""
    if m["role"] == "assistant" and m.get("tool_calls"):
        d["tool_calls"] = [{"function": tc.function, "args": dict(tc.args)} for tc in m["tool_calls"]]
    if m["role"] == "tool":
        d["error"] = m.get("error")
        tc = m.get("tool_call")
        d["tool"] = tc.function if tc else None
    return d


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
        from agentgate.attack_trajectories import attack_calls

        if self.plan is None:
            self.plan = list(self.user_task.ground_truth(env.model_copy(deep=True)))
        last = messages[-1] if messages else None
        if last is not None and last["role"] == "tool":
            text = get_text_content_as_str(last["content"]) + (last.get("error") or "")
            if "blocked by security policy" in text and self.on_block == "stop":
                self.stopped = True
            if (self.injection_task is not None and not self.hijacked and self.marker in text):
                self.hijacked = True
                inj, _ = attack_calls(self.suite_name, self.injection_task, env)
                self.plan = list(inj) + self.plan
        if self.plan and not self.stopped:
            call = self.plan.pop(0)
            msg = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string("")],
                                       tool_calls=[call])
        else:
            outs = [self.user_task.GROUND_TRUTH_OUTPUT]
            if self.hijacked and self.injection_task is not None:
                outs.append(self.injection_task.GROUND_TRUTH_OUTPUT)
            msg = ChatAssistantMessage(role="assistant",
                                       content=[text_content_block_from_string("\n".join(o for o in outs if o))],
                                       tool_calls=None)
        return query, runtime, env, [*messages, msg], extra_args
