"""긴 툴 응답을 겹치는 조각으로 나눈다.

두 OSS 판별기 모두 512토큰에서 잘라 한 번만 본다(LLM Guard 기본 match_type=FULL, PIGuard pipeline
truncation=True). 응답 뒤쪽에 심은 주입을 놓치지 않으려면 조각 단위로 검사해야 한다.
"""

from __future__ import annotations


def split_chunks(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """[start, end) 문자 구간 목록. 마지막 조각은 텍스트 끝에 맞춘다. 빈 텍스트는 빈 목록."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    if not text:
        return []
    if len(text) <= size:
        return [(0, len(text))]
    step = size - overlap
    spans, start = [], 0
    while start + size < len(text):
        spans.append((start, start + size))
        start += step
    spans.append((max(0, len(text) - size), len(text)))
    return spans
