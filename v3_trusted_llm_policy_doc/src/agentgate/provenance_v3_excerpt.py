"""발췌: src/agentgate/provenance.py — 이 버전의 정의만 원문 그대로 옮긴 읽기용 사본이다.
import와 공유 helper는 원본에 있다. 생성: scripts/reporting/sync_version_folders.py
"""
# src/agentgate/provenance.py :: is_delegated_source_response
def is_delegated_source_response(tool: str, args: dict, spec: TaskSpec, min_len: int = 4) -> bool:
    """v3: the response comes from a source whose instructions the user asked the assistant to carry out."""
    delegated = getattr(spec, "delegated_sources", None) or []
    if not delegated:
        return False
    return is_named_source_response(tool, args, spec.model_copy(update={"named_sources": list(delegated)}), min_len)


# src/agentgate/provenance.py :: ValueIndex.instructed_requests
def instructed_requests(self, tool: str, args: dict) -> list[dict]:
    """v3: ToolSpec requests (from any tool response) that this call carries out.

    A request matches when its action is this tool, or its action is unknown ("other"), and one of its target
    values that the user did not supply occurs in the call's arguments (normalized, either direction).
    """
    argument_values = [normalize_value(v) for v in iterate_leaf_values(args) if str(v).strip()]
    matches = []
    for source in self.sources[1:]:
        spec = source.toolspec
        if not spec:
            continue
        authors = {r["id"]: r.get("author") for r in spec.get("records", [])}
        for request in spec.get("requests", []):
            if request["action"] not in (tool, "other"):
                continue
            # a value the user supplied (prompt, own identity, trusted directory) is no evidence that the call
            # follows the tool output, so only untrusted target values count
            hit = [t for t in request["targets"] if _target_in_args(normalize_value(t), argument_values)
                   and self.origin_of(t).origin not in TRUSTED_ORIGINS]
            if not hit:
                continue
            matches.append({"source_id": source.source_id, "tool": source.tool, "record": request["record"],
                            "author": authors.get(request["record"]), "addressed_to": request["addressed_to"],
                            "action": request["action"], "targets": hit, "named": source.is_named,
                            "delegated": source.is_delegated})
    return matches
