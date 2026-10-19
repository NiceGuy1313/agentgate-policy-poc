"""AgentDojo 작업의 저장된 TaskSpec loader: 사람이 검토한 oracle TaskSpec(data/agentdojo/oracle_specs/)과
저장된 LLM TaskSpec(data/cache/specs/<model>/, scripts/taskspec/extract_llm_specs.py). 경로가 (suite, user task)
단위라 AgentDojo 바인딩에 둔다."""

from __future__ import annotations

from pathlib import Path

from agentgate.config import PROJECT_ROOT
from agentgate.core.task_spec import TaskSpec, llm_spec_dir


def oracle_spec_dir(root: Path | None = None) -> Path:
    return (root or PROJECT_ROOT) / "data" / "agentdojo" / "oracle_specs"


def load_task_spec(kind: str, suite: str, user_task_id: str, root: Path | None = None) -> TaskSpec:
    """kind = 'oracle' (data/agentdojo/oracle_specs) or 'llm:<model>' (data/cache/specs/<model>)."""
    if kind == "oracle":
        path = oracle_spec_dir(root) / suite / f"{user_task_id}.yaml"
    elif kind.startswith("llm:"):
        path = llm_spec_dir(kind.split(":", 1)[1], root) / suite / f"{user_task_id}.yaml"
    else:
        raise ValueError(f"unknown spec kind {kind}")
    return TaskSpec.load(path)
