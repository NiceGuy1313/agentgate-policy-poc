"""오프라인 provenance 분석(scripts/benchmark)과 runtime ValueIndex가 공유하는 값 정규화이다.

규칙:
- e-mail: 소문자화
- URL: 소문자화, scheme·앞의 "www."·끝의 "/" 제거
- IBAN: 공백 제거, 대문자화
- list 인자는 원소별로 판정한다(`iterate_leaf_values` 참조)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){10,30}\b")
URL_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}(?:/[^\s'\"<>),]*)?")
PHONE_PATTERN = re.compile(r"\+?\d[\d ()-]{7,}\d")
AMOUNT_PATTERN = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_SCHEME_PATTERN = re.compile(r"^[a-z]+://")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def classify_value_kind(value: str) -> str:
    stripped = value.strip()
    if EMAIL_PATTERN.fullmatch(stripped):
        return "email"
    if re.fullmatch(r"[A-Za-z]{2}\d{2}[A-Za-z0-9 ]{10,40}", stripped):
        return "iban"
    if re.fullmatch(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/\S*)?", stripped) and "." in stripped:
        return "url"
    return "text"


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_url(value: str) -> str:
    value = value.strip().lower()
    value = _SCHEME_PATTERN.sub("", value)
    if value.startswith("www."):
        value = value[4:]
    return value.rstrip("/")


def normalize_iban(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def normalize_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value.strip().lower())


def normalize_value(value: Any) -> str:
    text = str(value)
    kind = classify_value_kind(text)
    if kind == "email":
        return normalize_email(text)
    if kind == "url":
        return normalize_url(text)
    if kind == "iban":
        return normalize_iban(text)
    return normalize_text(text)


def build_haystack(text: str) -> dict[str, str]:
    """Normalized views of a text blob used for containment checks."""
    lowered = text.lower()
    url_view = re.sub(r"https?://", "", lowered).replace("www.", "")
    return {
        "text": _WHITESPACE_PATTERN.sub(" ", lowered),
        "url": url_view,
        "compact": re.sub(r"\s+", "", text).upper(),
    }


def haystack_contains(haystack: dict[str, str], value: Any) -> bool:
    """True if `value` (normalized according to its kind) occurs in the normalized haystack."""
    text = str(value)
    if not text.strip():
        return False
    kind = classify_value_kind(text)
    if kind == "iban":
        return normalize_iban(text) in haystack["compact"]
    if kind == "url":
        return normalize_url(text) in haystack["url"]
    if kind == "email":
        return normalize_email(text) in haystack["text"]
    normalized_value = normalize_text(text)
    if len(normalized_value) < 3:
        return False
    # word-boundary match for short word-like values ("Bob" must not match "Bobby")
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_value) + r"(?![a-z0-9])"
    return re.search(pattern, haystack["text"]) is not None


def iterate_leaf_values(value: Any) -> Iterable[Any]:
    """Element-wise iteration for list arguments; scalars yield themselves."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        leaves = []
        for item in value:
            leaves.extend(iterate_leaf_values(item))
        return leaves
    if isinstance(value, dict):
        leaves = []
        for item in value.values():
            leaves.extend(iterate_leaf_values(item))
        return leaves
    return [value]


def flatten_string_fields(obj: Any, min_len: int = 1) -> list[str]:
    """All string-ish leaf values of a (pydantic / dict / list) object."""
    if obj is None:
        return []
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    strings: list[str] = []
    if isinstance(obj, dict):
        for item in obj.values():
            strings.extend(flatten_string_fields(item, min_len))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            strings.extend(flatten_string_fields(item, min_len))
    else:
        text = str(obj)
        if len(text) >= min_len:
            strings.append(text)
    return strings


def parse_amount(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def text_contains_amount(amount: float, text: str) -> bool:
    for match in AMOUNT_PATTERN.finditer(text.replace(",", "")):
        try:
            if abs(float(match.group(0)) - amount) < 1e-6:
                return True
        except ValueError:
            continue
    return False


def extract_typed_candidates(text: str) -> dict[str, set[str]]:
    """Typed candidate values in a text (for reporting and judge candidate counts)."""
    return {
        "email": {normalize_email(m) for m in EMAIL_PATTERN.findall(text)},
        "iban": {normalize_iban(m) for m in IBAN_PATTERN.findall(text)},
        "url": {normalize_url(m) for m in URL_PATTERN.findall(text) if "@" not in m},
        "phone": {re.sub(r"\D", "", m) for m in PHONE_PATTERN.findall(text)},
        "amount": set(AMOUNT_PATTERN.findall(text)),
    }
