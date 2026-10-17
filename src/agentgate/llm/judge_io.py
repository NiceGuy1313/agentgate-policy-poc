"""ESCALATE 판정 LLM(v2·v3)이 함께 쓰는 입력·출력 helper: 값 감싸기, 출처 표기, 답 해석."""

from __future__ import annotations

import re

from agentgate.gate.base import PolicyContext
from agentgate.gate.normalization import long_value_candidates

LONG_VALUE_CHARS = 200


def data_block(text: str, limit: int) -> str:
    text = str(text)
    clipped = text[:limit] + (" …[truncated]" if len(text) > limit else "")
    return f"<data>{clipped}</data>"


def origin_label(provenance) -> str:
    if provenance.step is not None and provenance.step >= 0:
        return f"step{provenance.step}:{provenance.tool} ({provenance.origin.value})"
    return provenance.origin.value


def argument_origins(value, context: PolicyContext) -> list[tuple[str, object]]:
    """(value, ProvenanceResult) for a leaf value; long texts are traced through the typed values inside."""
    index = context.value_index
    text = str(value)
    if len(text) <= LONG_VALUE_CHARS:
        return [(text, index.origin_of(value))]
    return [(c, index.origin_of(c)) for c in long_value_candidates(text, LONG_VALUE_CHARS)]


def parse_trusted_answer(text: str) -> str:
    match = re.match(r"\s*[*#`_ ]*(ALLOW|BLOCK|HUMAN)\b", text.upper())
    return match.group(1) if match else "HUMAN"
