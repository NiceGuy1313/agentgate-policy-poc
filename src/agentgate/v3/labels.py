"""v3 ToolSpec 라벨: 툴 응답에서 뽑은 요청을 호출과 대조한다(P11, 판정 LLM v3 입력). 위임 자료 판별도 여기 있다."""

from __future__ import annotations

from agentgate.gate.normalization import iterate_leaf_values, normalize_value
from agentgate.gate.provenance import TRUSTED_ORIGINS, ValueIndex, is_named_source_response
from agentgate.gate.task_spec import TaskSpec


def is_delegated_source_response(tool: str, args: dict, spec: TaskSpec, min_len: int = 4) -> bool:
    """The response comes from a source whose instructions the user asked the assistant to carry out."""
    delegated = getattr(spec, "delegated_sources", None) or []
    if not delegated:
        return False
    return is_named_source_response(tool, args, spec.model_copy(update={"named_sources": list(delegated)}), min_len)


def instructed_requests(index: ValueIndex, tool: str, args: dict) -> list[dict]:
    """ToolSpec requests (from any tool response) that this call carries out.

    A request matches when its action is this tool, or its action is unknown ("other"), and one of its target
    values that the user did not supply occurs in the call's arguments (normalized, either direction).
    """
    argument_values = [normalize_value(v) for v in iterate_leaf_values(args) if str(v).strip()]
    matches = []
    for source in index.sources[1:]:
        spec = source.labels.get("toolspec")
        if not spec:
            continue
        authors = {r["id"]: r.get("author") for r in spec.get("records", [])}
        for request in spec.get("requests", []):
            if request["action"] not in (tool, "other"):
                continue
            # a value the user supplied (prompt, own identity, trusted directory) is no evidence that the call
            # follows the tool output, so only untrusted target values count
            hit = [t for t in request["targets"] if _target_in_args(normalize_value(t), argument_values)
                   and index.origin_of(t).origin not in TRUSTED_ORIGINS]
            if not hit:
                continue
            matches.append({"source_id": source.source_id, "tool": source.tool, "record": request["record"],
                            "author": authors.get(request["record"]), "addressed_to": request["addressed_to"],
                            "action": request["action"], "targets": hit, "named": source.is_named,
                            "delegated": bool(source.labels.get("delegated"))})
    return matches


def _target_in_args(target: str, argument_values: list[str]) -> bool:
    if len(target) < 3:
        return False
    return any(target == v or (len(v) >= 3 and (target in v or v in target)) for v in argument_values)
