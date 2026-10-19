"""룰북 정적 검사기의 사실 계산(`_Facts`)과 값 일치 helper(target_hit, amount_hit, 위임 작성자 일치). LLM 호출은 없다.

`agentgate.policy_gate.rule_checker`가 호출마다 `_Facts`를 한 번 만들고 규칙 R1–R8이 그 값을 읽는다. 용어(지목 자료,
근거 있는 값, target 일치, 위임)는 rule_checker 모듈 설명을 따른다.
"""

from __future__ import annotations

import re
from functools import cached_property

from agentgate.core.binding import ToolBinding
from agentgate.core.normalization import (
    EMAIL_PATTERN,
    classify_value_kind,
    haystack_contains,
    iterate_leaf_values,
    normalize_text,
    normalize_url,
    normalize_value,
    parse_amount,
    text_contains_amount,
)
from agentgate.core.provenance import (
    TRUSTED_ORIGINS,
    IndexedSource,
    Origin,
    is_named_source_response,
    names_match,
)
from agentgate.core.types import PolicyContext, ToolCall
from agentgate.policy_gate.toolspec_schema import CLAIM_KEYS, DATA_KEYS, TARGET_KEYS

MATCH_ROLES = ("destination", "url", "password", "target_id", "booking")   # + amount (numeric match)
# bare domains must not be the domain part of an e-mail address ("x@clientcorp.com" is not a link)
URL_IN_TEXT = re.compile(
    r"(?:https?://|(?<![@\w.-])www\.)[^\s'\"<>)]+"
    r"|(?<![@\w.-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ly|info)\b(?:/[^\s'\"<>)]*)?", re.IGNORECASE)
CONTAINMENT_MIN_LEN = 4   # names/texts targets: token-boundary containment, shorter side at least this long
_EPS = 1e-6


def _file_ids(value) -> list[str]:
    if isinstance(value, dict):
        return [str(value[k]) for k in ("file_id", "id") if value.get(k) is not None][:1]
    if isinstance(value, (list, tuple)):
        return [i for item in value for i in _file_ids(item)]
    return [str(value)] if value is not None and str(value).strip() else []


def _host(url: str) -> str:
    return normalize_url(str(url)).split("/", 1)[0]


def target_hit(value, targets: dict) -> bool:
    """`value` (a call argument) is one of the request's typed targets: equal after normalization, the same URL host,
    or (names, texts) contained in either direction on token boundaries."""
    normalized = normalize_value(value)
    is_url = classify_value_kind(str(value)) == "url"
    for key in TARGET_KEYS:
        if key == "amounts":
            continue
        for target in targets.get(key) or []:
            target_normalized = normalize_value(target)
            if not target_normalized:
                continue
            if target_normalized == normalized:
                return True
            if is_url and (key == "urls" or classify_value_kind(str(target)) == "url") \
                    and _host(target) == _host(value):
                return True
            if key in ("names", "texts") and names_match(target_normalized, normalized, CONTAINMENT_MIN_LEN):
                return True
    return False


def amount_hit(amount: float, targets: dict) -> bool:
    return any(x is not None and abs(amount - x) < _EPS for x in map(parse_amount, targets.get("amounts") or []))


def _is_person(entry: str) -> bool:
    """A delegated entry naming a person (or an e-mail address) rather than a file, URL, channel id or tool."""
    entry = entry.strip()
    return "@" in entry or not (entry.startswith("tool:") or "." in entry or "/" in entry)


def _author_matches(entry: str, author: str) -> bool:
    """Exact name or address, or the e-mail local part ("david.smith" for "David Smith"); no partial tokens, so
    "david.x@evil.com" does not match "David"."""
    entry_n, author_n = normalize_text(entry), normalize_text(author)
    if entry_n == author_n:
        return True
    tokens = re.findall(r"[a-z0-9]+", entry_n)
    names = [author_n.split("<")[0].strip()] + re.findall(r"[\w.+-]+@[\w.-]+", author_n)
    for name in names:
        if not name:
            continue
        if "@" in name:
            if "@" in entry_n:
                if entry_n == name:
                    return True
                continue
            if re.findall(r"[a-z0-9]+", name.split("@")[0]) == tokens:
                return True
        elif re.findall(r"[a-z0-9]+", name) == tokens:
            return True
    return False


class _Facts:
    """Everything the rules read for one pending call, computed once."""

    def __init__(self, call: ToolCall, context: PolicyContext, roles: dict[str, list[str]], outgoing: bool,
                 binding: ToolBinding | None = None):
        self.call, self.spec, self.index, self.session = call, context.spec, context.value_index, context.session
        self.binding = binding
        self.args: dict[str, list] = {
            role: [v for name in names for v in iterate_leaf_values(call.args.get(name)) if str(v).strip()]
            for role, names in roles.items() if role != "attachment"}
        self.attachments = [i for name in roles.get("attachment", []) for i in _file_ids(call.args.get(name))]
        self.content = "\n".join(str(v) for v in self.args.get("content", []))
        self.content_urls = ([u.rstrip(".,;") for u in URL_IN_TEXT.findall(EMAIL_PATTERN.sub(" ", self.content))]
                             if outgoing else [])
        self.named_data: set[str] = set()
        self.named_amounts: list[float] = []
        # (source, record or None, request, number of records in the response)
        self.requests: list[tuple[IndexedSource, dict | None, dict, int]] = []
        for source in self.index.sources[1:]:
            spec = source.labels.get("toolspec")
            if not isinstance(spec, dict):
                continue   # extraction failed: no records, no requests
            records = {r.get("id"): r for r in spec.get("records", [])}
            if source.is_named:
                for record in records.values():
                    data = record.get("data") or {}
                    for key in DATA_KEYS:
                        for value in data.get(key) or []:
                            if key == "amounts":
                                amount = parse_amount(value)
                                if amount is not None:
                                    self.named_amounts.append(amount)
                            else:
                                self.named_data.add(normalize_value(value))
            for request in spec.get("requests", []):
                self.requests.append((source, records.get(request.get("record")), request, len(records)))

    # ---------------------------------------------------------------- R9 delegation
    def delegated_entries(self, source: IndexedSource) -> list[str]:
        """delegated_sources entries the response matches (named-source matching, one entry at a time)."""
        min_len = self.index.named_match_min_len
        return [d for d in self.spec.delegated_sources
                if str(d).strip() and is_named_source_response(source.tool, source.args,
                                                               self.spec.model_copy(update={"named_sources": [d]}),
                                                               min_len, self.binding)]

    def pointed(self, source: IndexedSource) -> bool:
        """A response the user pointed to: a named source or a delegated source."""
        return source.is_named or bool(self.delegated_entries(source))

    def delegated(self, source: IndexedSource, record: dict | None, n_records: int) -> bool:
        """The request's response is a delegated source (named-source matching against delegated_sources) and, for a
        person entry, its record's author is that person. A request without a record inherits delegation only when
        the response has at most one record."""
        matched = self.delegated_entries(source)
        if not matched:
            return False
        if record is None:
            return n_records <= 1
        if not any(_is_person(d) for d in matched):
            return True
        author = record.get("author")
        if not author:
            return n_records <= 1
        return any(_author_matches(d, author) for d in matched if _is_person(d))

    @cached_property
    def delegated_targets(self) -> list[dict]:
        """Targets of delegated requests for this action (same tool, or no specific tool): the user's own values."""
        return [request.get("targets") or {} for source, record, request, n in self.requests
                if request.get("action") in (self.call.tool, "other", None, "")
                and self.delegated(source, record, n)]

    # ---------------------------------------------------------------- value grounding
    def origin(self, value) -> Origin:
        return self.index.origin_of(value).origin

    def by_delegation(self, value) -> bool:
        return any(target_hit(value, targets) for targets in self.delegated_targets)

    def grounded(self, value) -> bool:
        return (self.origin(value) in TRUSTED_ORIGINS or normalize_value(value) in self.named_data
                or self.by_delegation(value))

    def amount_grounded(self, amount: float) -> bool:
        cap = self.spec.amount_cap
        return ((cap is not None and amount <= cap + _EPS) or text_contains_amount(amount, self.index.prompt.text)
                or any(abs(amount - x) < _EPS for x in self.named_amounts)
                or any(amount_hit(amount, targets) for targets in self.delegated_targets))

    def in_prompt(self, value) -> bool:
        """Prompt containment; short ids ("13") as a standalone token, which the index haystack skips. A token inside
        a time or date ("10:00", "2024-05-13") does not count."""
        if self.origin(value) == Origin.PROMPT or haystack_contains(self.index.prompt.haystack, value):
            return True
        word = normalize_text(str(value))
        pattern = (r"(?<![a-z0-9])(?<![0-9][:/.-])" + re.escape(word) + r"(?![a-z0-9])(?![:/.-][0-9])")
        return bool(word) and re.search(pattern, normalize_text(self.index.prompt.text)) is not None

    def from_prompt_or_named(self, value) -> bool:
        return self.in_prompt(value) or self.origin(value) in TRUSTED_ORIGINS | {Origin.NAMED_SOURCE}

    # ---------------------------------------------------------------- R2 / R8
    @cached_property
    def matches(self) -> list[dict]:
        """Requests the call carries out (R2 (a)/(b)), each with its delegation flag and claims."""
        candidates = [(role, v) for role in MATCH_ROLES for v in self.args.get(role, [])]
        candidates += [("url", u) for u in self.content_urls]
        amounts = [a for a in (parse_amount(v) for v in self.args.get("amount", [])) if a is not None]
        out = []
        for source, record, request, n_records in self.requests:
            targets = request.get("targets") or {}
            delegated = self.delegated(source, record, n_records)
            # a delegated request's own targets are grounded by it; judge its values without that grounding
            hits = [f"{role} '{v}'" for role, v in candidates if target_hit(v, targets)
                    and (delegated or not self.grounded(v))]
            hits += [f"amount {a}" for a in amounts
                     if amount_hit(a, targets) and (delegated or not self.amount_grounded(a))]
            if request.get("action") == self.call.tool and (delegated or self.call.tool not in self.spec.requested_ops):
                hits.append(f"action {self.call.tool}")
            if hits:
                claims = [k for k in CLAIM_KEYS if (request.get("claims") or {}).get(k)]
                out.append({"where": f"{source.source_id}/{request.get('id') or request.get('record')}",
                            "hits": hits, "delegated": delegated, "claims": claims})
        return out
