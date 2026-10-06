"""Value normalization shared by Stage 1 provenance analysis and the runtime ValueIndex.

Rules (Stage 1 task 4):
- e-mail: lower-case
- URL: lower-case, drop scheme, leading "www.", trailing "/"
- IBAN: drop whitespace, upper-case
- list arguments are judged element by element (see `iter_values`)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){10,30}\b")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}(?:/[^\s'\"<>),]*)?")
PHONE_RE = re.compile(r"\+?\d[\d ()-]{7,}\d")
AMOUNT_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_SCHEME = re.compile(r"^[a-z]+://")
_WS = re.compile(r"\s+")


def kind_of(value: str) -> str:
    v = value.strip()
    if EMAIL_RE.fullmatch(v):
        return "email"
    if re.fullmatch(r"[A-Za-z]{2}\d{2}[A-Za-z0-9 ]{10,40}", v):
        return "iban"
    if re.fullmatch(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/\S*)?", v) and "." in v:
        return "url"
    return "text"


def norm_email(v: str) -> str:
    return v.strip().lower()


def norm_url(v: str) -> str:
    v = v.strip().lower()
    v = _SCHEME.sub("", v)
    if v.startswith("www."):
        v = v[4:]
    return v.rstrip("/")


def norm_iban(v: str) -> str:
    return re.sub(r"\s+", "", v).upper()


def norm_text(v: str) -> str:
    return _WS.sub(" ", v.strip().lower())


def norm_value(v: Any) -> str:
    s = str(v)
    k = kind_of(s)
    if k == "email":
        return norm_email(s)
    if k == "url":
        return norm_url(s)
    if k == "iban":
        return norm_iban(s)
    return norm_text(s)


def norm_haystack(text: str) -> dict[str, str]:
    """Normalized views of a text blob used for containment checks."""
    low = text.lower()
    url_view = re.sub(r"https?://", "", low).replace("www.", "")
    return {
        "text": _WS.sub(" ", low),
        "url": url_view,
        "compact": re.sub(r"\s+", "", text).upper(),
    }


def contains(haystack: dict[str, str], value: Any) -> bool:
    """True if `value` (normalized according to its kind) occurs in the normalized haystack."""
    s = str(value)
    if not s.strip():
        return False
    k = kind_of(s)
    if k == "iban":
        return norm_iban(s) in haystack["compact"]
    if k == "url":
        return norm_url(s) in haystack["url"]
    if k == "email":
        return norm_email(s) in haystack["text"]
    nv = norm_text(s)
    if len(nv) < 3:
        return False
    # word-boundary match for short word-like values ("Bob" must not match "Bobby")
    pattern = r"(?<![a-z0-9])" + re.escape(nv) + r"(?![a-z0-9])"
    return re.search(pattern, haystack["text"]) is not None


def iter_values(v: Any) -> Iterable[Any]:
    """Element-wise iteration for list arguments; scalars yield themselves."""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            out.extend(iter_values(x))
        return out
    if isinstance(v, dict):
        out = []
        for x in v.values():
            out.extend(iter_values(x))
        return out
    return [v]


def flatten_strings(obj: Any, min_len: int = 1) -> list[str]:
    """All string-ish leaf values of a (pydantic / dict / list) object."""
    if obj is None:
        return []
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(flatten_strings(v, min_len))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            out.extend(flatten_strings(v, min_len))
    else:
        s = str(obj)
        if len(s) >= min_len:
            out.append(s)
    return out


def parse_amount(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def amount_in_text(amount: float, text: str) -> bool:
    for m in AMOUNT_RE.finditer(text.replace(",", "")):
        try:
            if abs(float(m.group(0)) - amount) < 1e-6:
                return True
        except ValueError:
            continue
    return False


def extract_candidates(text: str) -> dict[str, set[str]]:
    """Typed candidate values in a text (for reporting and judge candidate counts)."""
    return {
        "email": {norm_email(m) for m in EMAIL_RE.findall(text)},
        "iban": {norm_iban(m) for m in IBAN_RE.findall(text)},
        "url": {norm_url(m) for m in URL_RE.findall(text) if "@" not in m},
        "phone": {re.sub(r"\D", "", m) for m in PHONE_RE.findall(text)},
        "amount": set(AMOUNT_RE.findall(text)),
    }
