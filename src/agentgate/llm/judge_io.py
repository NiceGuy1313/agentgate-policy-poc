"""ESCALATE 판정 LLM(v3)이 쓰는 입력 helper: 값 감싸기."""

from __future__ import annotations


def data_block(text: str, limit: int) -> str:
    text = str(text)
    clipped = text[:limit] + (" …[truncated]" if len(text) > limit else "")
    return f"<data>{clipped}</data>"
