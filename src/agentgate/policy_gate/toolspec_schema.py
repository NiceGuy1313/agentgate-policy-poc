"""ToolSpec 표준 스키마와 검증(`validate_toolspec`). LLM을 쓰지 않으므로 정적 검사기(rule_checker)도 이 모듈만 import한다.

스키마는 항상 같다. 검증 후에는 모든 키가 존재한다.
  {"records": [{"id", "kind", "author", "data": {DATA_KEYS}}],
   "requests": [{"id", "record", "addressed_to", "action", "claims": {CLAIM_KEYS}, "targets": {TARGET_KEYS}}]}
값(author, data, targets)은 툴 응답 원문에 실제로 있고 타입이 맞는 것만 남긴다. 추출기는 `agentgate.policy_gate.toolspec`이다.
"""

from __future__ import annotations

import math

from agentgate.core.normalization import (
    EMAIL_PATTERN,
    IBAN_PATTERN,
    URL_PATTERN,
    normalize_email,
    normalize_iban,
    normalize_text,
    normalize_url,
    normalize_value,
    parse_amount,
    text_contains_amount,
)

RECORD_KINDS = {"email", "message", "file", "event", "review", "transaction", "webpage", "contact", "listing",
                "other"}
ADDRESSEES = {"assistant", "user", "unclear"}
DATA_KEYS = ("emails", "ibans", "urls", "amounts", "names", "files")   # records[].data
TARGET_KEYS = DATA_KEYS + ("texts",)                                    # requests[].targets
CLAIM_KEYS = ("authority", "override", "urgency", "conceal")           # requests[].claims
EMPTY_DATA = {key: [] for key in DATA_KEYS}                             # read-only default; copy before changing


def _appears(value: str, haystack: str, haystack_normalized: str) -> bool:
    value = str(value).strip()
    if not value:
        return False
    return (value.lower() in haystack or normalize_text(value) in haystack_normalized
            or normalize_value(value) in haystack_normalized)


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _clean_value(key: str, value, output_text: str, haystack: str, haystack_normalized: str) -> str | float | None:
    """The normalized value if it occurs in the output and matches the type of `key`, else None."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if key == "amounts":
        amount = parse_amount(value)
        if amount is None or not math.isfinite(amount):
            return None
        # numeric match too: "1,000.50" in the output may come back as 1000.5
        if text_contains_amount(amount, output_text) or _appears(value, haystack, haystack_normalized):
            return amount
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if key == "ibans":
        iban = normalize_iban(text)
        # the output may write the IBAN with spaces
        found = _appears(value, haystack, haystack_normalized) or iban in normalize_iban(output_text)
        return iban if found and IBAN_PATTERN.fullmatch(iban) else None
    if not _appears(value, haystack, haystack_normalized):
        return None
    if key == "emails":
        return normalize_email(text) if EMAIL_PATTERN.fullmatch(text) else None
    if key == "urls":
        url = normalize_url(text)
        return url if URL_PATTERN.fullmatch(url) else None
    return text   # names, files, texts: verbatim check only


def validate_toolspec(raw: dict, output_text: str, tool_names: set[str]) -> tuple[dict, int]:
    """Fill the fixed schema: missing keys get defaults, unknown keys are dropped, ids are r1.. / q1.., and every
    value must occur in the tool output and match its type. Returns (spec, number of dropped values)."""
    haystack = output_text.lower()
    haystack_normalized = normalize_text(output_text)
    dropped = 0

    def typed(values, keys: tuple[str, ...]) -> dict:
        nonlocal dropped
        values = values if isinstance(values, dict) else {}
        result = {}
        for key in keys:
            items = values.get(key)
            if not isinstance(items, list):
                items = [] if items is None else [items]
            kept = []
            for item in items:
                cleaned = _clean_value(key, item, output_text, haystack, haystack_normalized)
                if cleaned is None:
                    dropped += 1
                elif cleaned not in kept:
                    kept.append(cleaned)
            result[key] = kept
        return result

    records = []
    record_ids = {}   # the id the model used (or the normalized id) -> normalized id
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            continue
        record_id = f"r{len(records) + 1}"
        if record.get("id"):
            record_ids.setdefault(str(record["id"]), record_id)
        author = record.get("author")
        if isinstance(author, str) and _appears(author, haystack, haystack_normalized):
            author = author.strip()
        else:
            if author is not None:
                dropped += 1
            author = None
        records.append({"id": record_id, "kind": record.get("kind") if record.get("kind") in RECORD_KINDS else "other",
                        "author": author, "data": typed(record.get("data"), DATA_KEYS)})
    for record in records:
        record_ids.setdefault(record["id"], record["id"])
    requests = []
    for request in raw.get("requests") or []:
        if not isinstance(request, dict):
            continue
        claims = request.get("claims") if isinstance(request.get("claims"), dict) else {}
        requests.append({
            "id": f"q{len(requests) + 1}",
            "record": record_ids.get(str(request["record"])) if request.get("record") is not None else None,
            "addressed_to": request.get("addressed_to") if request.get("addressed_to") in ADDRESSEES else "unclear",
            "action": request.get("action") if request.get("action") in tool_names else "other",
            "claims": {key: _as_bool(claims.get(key)) for key in CLAIM_KEYS},
            "targets": typed(request.get("targets"), TARGET_KEYS),
        })
    return {"records": records, "requests": requests}, dropped
