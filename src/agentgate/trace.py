"""Trace of tool calls, value index and provenance (origin) of argument values."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any

from agentgate import normalize as N
from agentgate.config import named_source_rules, sensitive_categories_of
from agentgate.spec import TaskSpec


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
class TraceEvent:
    step: int
    tool: str
    args: dict
    raw_output: Any = None
    output_text: str = ""
    error: str | None = None
    blocked: bool = False
    executed: bool = False
    phase: str = ""                   # replay bookkeeping: "user" | "injection" | "agent"
    decision: dict | None = None      # serialized EngineResult
    decide_ms: float = 0.0


@dataclass
class Source:
    sid: str                          # "prompt" or "step<N>"
    step: int                         # -1 for prompt
    tool: str | None
    args: dict
    text: str
    hay: dict[str, str]
    named: bool = False
    trusted: bool = False
    sensitive: list[str] = field(default_factory=list)
    kgrams: set[int] | None = None
    items: list[str] | None = None    # per-record strings for list-shaped responses (P3)
    fields: set[str] = field(default_factory=set)   # normalized exact string fields (short IDs)


@dataclass
class OriginResult:
    origin: Origin
    step: int | None = None
    tool: str | None = None
    sid: str | None = None

    def as_dict(self) -> dict:
        return {"origin": self.origin.value, "step": self.step, "tool": self.tool}


def _dict_keys(obj: Any) -> set[str]:
    """Keys of dict-shaped outputs are values too (e.g. file IDs keyed in a mapping)."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(N.norm_value(k))
            out |= _dict_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _dict_keys(v)
    return out


def _kgrams(text: str, k: int) -> set[int]:
    t = N.norm_text(text)
    if len(t) < k:
        return set()
    return {hash(t[i : i + k]) for i in range(len(t) - k + 1)}


def is_named_response(tool: str, args: dict, spec: TaskSpec) -> bool:
    if f"tool:{tool}" in spec.named_sources:
        return True
    rules = named_source_rules()["by_argument"].get(tool, [])
    if not rules or not spec.named_sources:
        return False
    for arg in rules:
        for v in N.iter_values(args.get(arg)):
            nv = N.norm_value(v)
            if not nv:
                continue
            for ns in spec.named_sources:
                nn = N.norm_value(ns)
                if nn and _name_match(nn, nv):
                    return True
    return False


def _name_match(named: str, arg: str) -> bool:
    """Named source `named` matches tool argument `arg` if one contains the other on token
    boundaries; the contained side must be at least 4 characters (so query "x" is not a match)."""
    def within(small, big):
        if len(small) < 4:
            return small == big
        return re.search(r"(?<![a-z0-9])" + re.escape(small) + r"(?![a-z0-9])", big) is not None
    return within(named, arg) or within(arg, named)


class ValueIndex:
    """Prompt + tool responses, searchable by normalized value.

    Values are found by normalized containment over two views of every response: the
    string handed to the model (output_text) and all string fields of the raw object.
    """

    def __init__(self, prompt: str, spec: TaskSpec, self_identifiers: list[str] | None = None,
                 min_string_field_len: int = 4):
        self.spec = spec
        self.min_len = min_string_field_len
        self.self_ids = [N.norm_value(s) for s in (self_identifiers or [])]
        self.sources: list[Source] = []
        self.allowed_hay = N.norm_haystack("\n".join(spec.allowed_dest))
        self.trusted_tools = set(named_source_rules().get("trusted_tools", []))
        self._add("prompt", -1, None, {}, prompt)

    def _add(self, sid, step, tool, args, text, named=False, trusted=False, sensitive=None) -> Source:
        src = Source(sid, step, tool, args, text, N.norm_haystack(text), named, trusted, sensitive or [])
        self.sources.append(src)
        return src

    @property
    def prompt(self) -> Source:
        return self.sources[0]

    def add_response(self, step: int, tool: str, args: dict, raw_output: Any, output_text: str) -> Source:
        fields = N.flatten_strings(raw_output, self.min_len)
        text = output_text + "\n" + "\n".join(fields)
        src = self._add(
            f"step{step}", step, tool, args, text,
            named=is_named_response(tool, args, self.spec),
            trusted=tool in self.trusted_tools,
            sensitive=sensitive_categories_of(tool),
        )
        src.fields = {N.norm_value(f) for f in N.flatten_strings(raw_output, 1)} | _dict_keys(raw_output)
        if isinstance(raw_output, (list, tuple)) and len(raw_output) > 1:
            src.items = ["\n".join(N.flatten_strings(it, 1)) for it in raw_output]
        return src

    # ------------------------------------------------------------------ provenance
    def occurrences(self, value: Any) -> list[Source]:
        nv = N.norm_value(value)
        return [s for s in self.sources if nv in s.fields or N.contains(s.hay, value)]

    def origin_of(self, value: Any) -> OriginResult:
        """PROMPT > ALLOWED_DEST > SELF > TRUSTED_TOOL > NAMED_SOURCE > OTHER_TOOL > UNSEEN.

        A value is judged by its most trusted occurrence; the evidence step is the first
        occurrence of that category (docs/design_decisions.md, 정책 해석 1).
        """
        nv = N.norm_value(value)
        if nv in self.self_ids:
            return OriginResult(Origin.SELF)
        occ = self.occurrences(value)
        if any(s.sid == "prompt" for s in occ):
            return OriginResult(Origin.PROMPT, -1, None, "prompt")
        if N.contains(self.allowed_hay, value):
            return OriginResult(Origin.ALLOWED_DEST)
        tool_occ = [s for s in occ if s.sid != "prompt"]
        for flag, origin in (("trusted", Origin.TRUSTED_TOOL), ("named", Origin.NAMED_SOURCE)):
            hit = [s for s in tool_occ if getattr(s, flag)]
            if hit:
                return OriginResult(origin, hit[0].step, hit[0].tool, hit[0].sid)
        if tool_occ:
            return OriginResult(Origin.OTHER_TOOL, tool_occ[0].step, tool_occ[0].tool, tool_occ[0].sid)
        return OriginResult(Origin.UNSEEN)

    def named_sources(self) -> list[Source]:
        return [s for s in self.sources if s.named]

    # ------------------------------------------------------------------ text overlap
    def overlap(self, sink_text: str, k: int, categories: list[str] | None = None) -> list[Source]:
        """Read responses sharing a substring of length >= k with `sink_text`
        (k-gram fingerprint intersection on normalized text)."""
        sink = _kgrams(sink_text, k) - _kgrams(self.prompt.text, k)   # text the user typed is not a leak
        if not sink:
            return []
        hits = []
        for s in self.sources[1:]:
            if categories is not None and not (set(s.sensitive) & set(categories)):
                continue
            if s.kgrams is None:
                s.kgrams = _kgrams(s.text, k)
            if sink & s.kgrams:
                hits.append(s)
        return hits

    def candidate_count(self, value: Any, sid: str | None) -> int:
        """Number of same-kind candidate values in the source that contains `value` (judge input)."""
        src = next((s for s in self.sources if s.sid == sid), None)
        if src is None:
            return 0
        kind = N.kind_of(str(value))
        cands = N.extract_candidates(src.text)
        return len(cands.get(kind, set())) if kind in cands else 0

    def context_snippets(self, value: Any, sid: str | None, width: int) -> list[str]:
        src = next((s for s in self.sources if s.sid == sid), None)
        if src is None:
            return []
        low = src.text.lower()
        key = N.norm_text(str(value))
        out, start = [], 0
        while True:
            i = low.find(key, start)
            if i < 0 or len(out) >= 3:
                break
            out.append(src.text[max(0, i - width) : i + len(key) + width])
            start = i + 1
        return out


def text_of_args(args: dict, names: list[str]) -> str:
    parts = []
    for n in names:
        for v in N.iter_values(args.get(n)):
            parts.append(str(v))
    return "\n".join(parts)
