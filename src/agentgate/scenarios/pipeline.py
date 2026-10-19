"""AgentDojo agent pipeline에 정책 게이트를 넣는다(agentdojo 소스 수정 없음).

게이트 자체는 task runtime으로 넘기는 `GatedRuntime`이다. pipeline 앞에는 element 하나(`TaskSpecElement`)만
추가되며, 이 element는 사용자 프롬프트만으로 TaskSpec을 만들고(LLM 추출기·캐시 또는 oracle spec) 첫 LLM 턴 전에
runtime을 설정한다.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Sequence

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.errors import AbortAgentError
from agentdojo.functions_runtime import EmptyEnv
from agentdojo.task_suite.task_suite import functions_stack_trace_from_messages, model_output_from_messages
from agentdojo.types import ChatMessage, get_text_content_as_str

from agentgate.core.runtime import GatedRuntime, compute_environment_hash
from agentgate.core.task_spec import TaskSpec

TaskSpecProvider = Callable[[str], tuple[TaskSpec, dict]]
"""prompt -> (TaskSpec, meta{source, cached, latency_ms, input_tokens, output_tokens})"""


class TaskSpecElement(BasePipelineElement):
    name = "taskspec"

    def __init__(self, provider: TaskSpecProvider):
        self.provider = provider

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        if isinstance(runtime, GatedRuntime) and runtime.index is None:
            start = time.perf_counter()
            spec, meta = self.provider(query)
            meta = {**meta, "wall_ms": (time.perf_counter() - start) * 1000}
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
            result = self.inner.query(query, runtime, env, messages, extra_args)
        except AbortAgentError as error:
            self.messages = error.messages
            raise
        self.messages = result[3]
        return result


def build_pipeline(llm: BasePipelineElement, spec_provider: TaskSpecProvider | None, defense: str | None = None,
                   system_message: str | None = None) -> AgentPipeline:
    config = PipelineConfig(llm=llm, model_id=None, defense=defense, system_message_name=None,
                            system_message=system_message)
    pipeline = AgentPipeline.from_config(config)
    if spec_provider is not None:
        # elements[:2] are the system message and the user prompt; the spec must exist before the first LLM turn
        pipeline.elements = [*pipeline.elements[:2], TaskSpecElement(spec_provider), *pipeline.elements[2:]]
    return pipeline


def run_live_task(suite, pipeline: RecordingPipeline, user_task, injection_task, injections: dict,
                  runtime: GatedRuntime) -> dict:
    """Same steps as TaskSuite.run_task_with_pipeline, but keeps env, messages and the gate trace."""
    environment = suite.load_and_inject_default_environment(injections)
    task_environment = user_task.init_environment(environment)
    pre_environment = task_environment.model_copy(deep=True)
    messages, model_output = [], None
    start = time.perf_counter()
    for _ in range(3):
        try:
            _, _, task_environment, messages, _ = pipeline.query(user_task.PROMPT, runtime, task_environment)
        except AbortAgentError as error:
            task_environment, messages = error.task_environment, error.messages
        model_output = model_output_from_messages(messages)
        if model_output is not None:
            break
    wall_ms = (time.perf_counter() - start) * 1000
    if model_output is None:
        warnings.warn(f"Model output was None for task {user_task.ID}")
    requested_calls = functions_stack_trace_from_messages(messages)
    result = score_task(suite, user_task, injection_task, model_output or [], pre_environment, task_environment,
                        requested_calls, runtime)
    result["wall_ms"] = wall_ms
    result["messages"] = [_serialize_message(m) for m in messages]
    result["final_output"] = get_text_content_as_str(model_output or [])[:2000]
    return result


def score_task(suite, user_task, injection_task, output, pre_environment, task_environment, requested_calls,
               runtime: GatedRuntime) -> dict:
    """AgentDojo utility / security for a finished run (shared by the native pipeline and framework runs)."""
    # checks may mutate the env (Inbox.get_unread): keep snapshots for the executed-only variant
    post_copy, pre_copy = task_environment.model_copy(deep=True), pre_environment.model_copy(deep=True)
    final_hash = compute_environment_hash(task_environment)
    utility = suite._check_task_result(user_task, output, pre_environment, task_environment, requested_calls)
    result = {"utility": bool(utility), "final_env_hash": final_hash,
              "n_tool_calls_requested": len(requested_calls)}
    if injection_task is not None:
        result["security"] = bool(
            suite._check_task_result(injection_task, output, pre_environment, task_environment, requested_calls))
        # executed calls only: match requested calls to gate events in order (unexecuted tail calls at the
        # loop limit and calls AgentDojo rejects never reach the gate)
        executed_calls, event_index = [], 0
        for call in requested_calls:
            if event_index < len(runtime.trace) and runtime.trace[event_index].tool == call.function:
                if runtime.trace[event_index].executed:
                    executed_calls.append(call)
                event_index += 1
        suite._check_task_result(user_task, output, pre_copy, post_copy, executed_calls)
        result["security_executed"] = bool(
            suite._check_task_result(injection_task, output, pre_copy, post_copy, executed_calls))
    return result


def _serialize_message(message) -> dict:
    serialized = {"role": message["role"]}
    content = message.get("content")
    serialized["content"] = (
        get_text_content_as_str(content)[:1500] if isinstance(content, list)
        else (content or "")[:1500] if content else ""
    )
    if message["role"] == "assistant" and message.get("tool_calls"):
        serialized["tool_calls"] = [{"function": tc.function, "args": dict(tc.args)} for tc in message["tool_calls"]]
    if message["role"] == "tool":
        serialized["error"] = message.get("error")
        tool_call = message.get("tool_call")
        serialized["tool"] = tool_call.function if tool_call else None
    return serialized
