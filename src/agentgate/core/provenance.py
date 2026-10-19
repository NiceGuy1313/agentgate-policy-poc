"""프롬프트와 툴 응답에 대한 값 인덱스를 만들고, 인자 값의 출처(origin)를 판정한다."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any

from agentgate.config import load_named_source_rules, sensitive_categories_for_tool
from agentgate.core.normalization import (
    build_haystack,
    classify_value_kind,
    extract_typed_candidates,
    flatten_string_fields,
    haystack_contains,
    iterate_leaf_values,
    normalize_text,
    normalize_value,
)
from agentgate.core.task_spec import TaskSpec


class Origin(str, enum.Enum):
    PROMPT = "PROMPT"
    ALLOWED_DEST = "ALLOWED_DEST"     # listed in spec.allowed_dest (treated like PROMPT)
    SELF = "SELF"                     # user's own identifier (config self_identifiers)
    TRUSTED_TOOL = "TRUSTED_TOOL"     # directory tools (config trusted_tools)
    NAMED_SOURCE = "NAMED_SOURCE"
    OTHER_TOOL = "OTHER_TOOL"
    UNSEEN = "UNSEEN"


TRUSTED_ORIGINS = {Origin.PROMPT, Origin.ALLOWED_DEST, Origin.SELF, Origin.TRUSTED_TOOL}


@dataclass
class IndexedSource:
    source_id: str                    # "prompt" or "step<N>"
    step: int                         # -1 for prompt
    tool: str | None
    args: dict
    text: str
    haystack: dict[str, str]
    is_named: bool = False
    is_trusted: bool = False
    sensitive_categories: list[str] = field(default_factory=list)
    kgram_hashes: set[int] | None = None                  # computed lazily by find_overlapping_sources
    record_texts: list[str] | None = None                 # per-record strings for list-shaped responses (R4 bulk egress)
    exact_values: set[str] = field(default_factory=set)   # normalized exact string fields (short IDs)
    labels: dict[str, Any] = field(default_factory=dict)  # labels (ToolSpec)


@dataclass
class ProvenanceResult:
    origin: Origin
    step: int | None = None
    tool: str | None = None
    source_id: str | None = None

    def as_dict(self) -> dict:
        return {"origin": self.origin.value, "step": self.step, "tool": self.tool}


def _collect_dict_keys(obj: Any) -> set[str]:
    """Keys of dict-shaped outputs are values too (e.g. file IDs keyed in a mapping)."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(normalize_value(key))
            keys |= _collect_dict_keys(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            keys |= _collect_dict_keys(value)
    return keys


def compute_kgram_hashes(text: str, k: int) -> set[int]:
    normalized = normalize_text(text)
    if len(normalized) < k:
        return set()
    return {hash(normalized[i : i + k]) for i in range(len(normalized) - k + 1)}


def is_named_source_response(tool: str, args: dict, spec: TaskSpec, min_len: int = 4) -> bool:
    if f"tool:{tool}" in spec.named_sources:
        return True
    argument_names = load_named_source_rules()["by_argument"].get(tool, [])
    if not argument_names or not spec.named_sources:
        return False
    for argument_name in argument_names:
        for value in iterate_leaf_values(args.get(argument_name)):
            normalized_argument = normalize_value(value)
            if not normalized_argument:
                continue
            for named_source in spec.named_sources:
                normalized_named = normalize_value(named_source)
                if normalized_named and _names_match(normalized_named, normalized_argument, min_len):
                    return True
    return False


def _names_match(named: str, arg: str, min_len: int = 4) -> bool:
    """Named source `named` matches tool argument `arg` if one contains the other on token
    boundaries; the contained side must be at least `min_len` characters (so query "x" is not a match).
    config/gate.yaml runtime.named_match_min_len can lower min_len (3: "Bob" matches "Bob's inbox")."""
    def within(small, big):
        if len(small) < min_len:
            return small == big
        return re.search(r"(?<![a-z0-9])" + re.escape(small) + r"(?![a-z0-9])", big) is not None
    return within(named, arg) or within(arg, named)


class ValueIndex:
    """Prompt + tool responses, searchable by normalized value.

    Values are found by normalized containment over two views of every response: the
    string handed to the model (output_text) and all string fields of the raw object.
    """

    def __init__(self, prompt: str, spec: TaskSpec, self_identifiers: list[str] | None = None,
                 min_string_field_len: int = 4, named_match_min_len: int = 4):
        self.spec = spec
        self.min_string_field_length = min_string_field_len
        self.named_match_min_len = named_match_min_len
        self.self_identifiers = [normalize_value(s) for s in (self_identifiers or [])]
        self.sources: list[IndexedSource] = []
        self.allowed_destination_haystack = build_haystack("\n".join(spec.allowed_dest))
        self.trusted_tools = set(load_named_source_rules().get("trusted_tools", []))
        self._add_source("prompt", -1, None, {}, prompt)

    def _add_source(self, source_id, step, tool, args, text, is_named=False, is_trusted=False,
                    sensitive_categories=None) -> IndexedSource:
        source = IndexedSource(source_id, step, tool, args, text, build_haystack(text), is_named, is_trusted,
                               sensitive_categories or [])
        self.sources.append(source)
        return source

    def is_named_response(self, tool: str, args: dict) -> bool:
        """Hook for subclass indexes that widen what counts as a named source."""
        return is_named_source_response(tool, args, self.spec, self.named_match_min_len)

    @property
    def prompt(self) -> IndexedSource:
        return self.sources[0]

    def add_response(self, step: int, tool: str, args: dict, raw_output: Any, output_text: str) -> IndexedSource:
        string_fields = flatten_string_fields(raw_output, self.min_string_field_length)
        text = output_text + "\n" + "\n".join(string_fields)
        source = self._add_source(
            f"step{step}", step, tool, args, text,
            is_named=self.is_named_response(tool, args),
            is_trusted=tool in self.trusted_tools,
            sensitive_categories=sensitive_categories_for_tool(tool),
        )
        source.exact_values = (
            {normalize_value(f) for f in flatten_string_fields(raw_output, 1)} | _collect_dict_keys(raw_output)
        )
        if isinstance(raw_output, (list, tuple)) and len(raw_output) > 1:
            source.record_texts = ["\n".join(flatten_string_fields(record, 1)) for record in raw_output]
        return source

    # ------------------------------------------------------------------ provenance
    def find_occurrences(self, value: Any) -> list[IndexedSource]:
        normalized_value = normalize_value(value)
        return [s for s in self.sources
                if normalized_value in s.exact_values or haystack_contains(s.haystack, value)]

    def origin_of(self, value: Any) -> ProvenanceResult:
        """PROMPT > ALLOWED_DEST > SELF > TRUSTED_TOOL > NAMED_SOURCE > OTHER_TOOL > UNSEEN.

        A value is judged by its most trusted occurrence; the evidence step is the first
        occurrence of that category (docs/architecture.md, section 8).
        """
        normalized_value = normalize_value(value)
        if normalized_value in self.self_identifiers:
            return ProvenanceResult(Origin.SELF)
        occurrences = self.find_occurrences(value)
        if any(s.source_id == "prompt" for s in occurrences):
            return ProvenanceResult(Origin.PROMPT, -1, None, "prompt")
        if haystack_contains(self.allowed_destination_haystack, value):
            return ProvenanceResult(Origin.ALLOWED_DEST)
        tool_occurrences = [s for s in occurrences if s.source_id != "prompt"]
        for flag, origin in (("is_trusted", Origin.TRUSTED_TOOL), ("is_named", Origin.NAMED_SOURCE)):
            hits = [s for s in tool_occurrences if getattr(s, flag)]
            if hits:
                return ProvenanceResult(origin, hits[0].step, hits[0].tool, hits[0].source_id)
        if tool_occurrences:
            first = tool_occurrences[0]
            return ProvenanceResult(Origin.OTHER_TOOL, first.step, first.tool, first.source_id)
        return ProvenanceResult(Origin.UNSEEN)

    def named_source_responses(self) -> list[IndexedSource]:
        return [s for s in self.sources if s.is_named]

    # ------------------------------------------------------------------ text overlap
    def find_overlapping_sources(self, sink_text: str, k: int,
                                 categories: list[str] | None = None) -> list[IndexedSource]:
        """Read responses sharing a substring of length >= k with `sink_text`
        (k-gram fingerprint intersection on normalized text)."""
        # text the user typed is not a leak
        sink_hashes = compute_kgram_hashes(sink_text, k) - compute_kgram_hashes(self.prompt.text, k)
        if not sink_hashes:
            return []
        hits = []
        for source in self.sources[1:]:
            if categories is not None and not (set(source.sensitive_categories) & set(categories)):
                continue
            if source.kgram_hashes is None:
                source.kgram_hashes = compute_kgram_hashes(source.text, k)
            if sink_hashes & source.kgram_hashes:
                hits.append(source)
        return hits

    def source_by_id(self, source_id: str | None) -> IndexedSource | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def count_same_kind_candidates(self, value: Any, source_id: str | None) -> int:
        """Number of same-kind candidate values in the source that contains `value` (judge input)."""
        source = next((s for s in self.sources if s.source_id == source_id), None)
        if source is None:
            return 0
        kind = classify_value_kind(str(value))
        candidates = extract_typed_candidates(source.text)
        return len(candidates.get(kind, set())) if kind in candidates else 0

    def extract_context_snippets(self, value: Any, source_id: str | None, width: int) -> list[str]:
        source = next((s for s in self.sources if s.source_id == source_id), None)
        if source is None:
            return []
        lowered = source.text.lower()
        key = normalize_text(str(value))
        snippets, start = [], 0
        while True:
            i = lowered.find(key, start)
            if i < 0 or len(snippets) >= 3:
                break
            snippets.append(source.text[max(0, i - width) : i + len(key) + width])
            start = i + 1
        return snippets


def join_argument_text(args: dict, names: list[str]) -> str:
    parts = []
    for name in names:
        for value in iterate_leaf_values(args.get(name)):
            parts.append(str(value))
    return "\n".join(parts)
