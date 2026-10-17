"""v2 판별기 라벨: 응답에 붙은 DetectionResult를 값 단위로 읽는다(P10, Trusted LLM v2 입력)."""

from __future__ import annotations

from typing import Any

from agentgate.gate.normalization import normalize_value
from agentgate.gate.provenance import ValueIndex


def attach_detection(source, detection, text: str) -> None:
    source.labels["detection"], source.labels["detected_text"] = detection, text


def value_detection(index: ValueIndex, value: Any, source_id: str | None) -> dict | None:
    """Detector label of the response `source_id` as seen from `value`.

    The value is located in the scored text; the score of the chunks covering it is used. If it cannot be
    located, the whole-response score stands in. None when the response has no label.
    """
    source = index.source_by_id(source_id)
    detection = source.labels.get("detection") if source is not None else None
    if detection is None:
        return None
    positions = _locate(source.labels.get("detected_text", ""), value)
    value_score = max(detection.covers(a, b) for a, b in positions) if positions else detection.score
    return {"source_id": source.source_id, "tool": source.tool, "response_score": round(detection.score, 4),
            "flagged": bool(detection.flagged), "threshold": detection.threshold,
            "value_score": round(value_score, 4), "value_located": bool(positions),
            "in_suspicious_span": value_score >= detection.threshold,
            "suspicious_spans": len(detection.suspicious_spans())}


def suspicious_excerpts(index: ValueIndex, source_id: str | None, limit: int = 3, width: int = 240) -> list[str]:
    source = index.source_by_id(source_id)
    detection = source.labels.get("detection") if source is not None else None
    if detection is None:
        return []
    text = source.labels.get("detected_text", "")
    spans = sorted(detection.suspicious_spans(), key=lambda s: -s.score)[:limit]
    return [text[s.start : s.end][:width] for s in spans]


def _locate(text: str, value: Any) -> list[tuple[int, int]]:
    """Case-insensitive [start, end) positions of `value` in `text` (raw form, then normalized form)."""
    lowered = text.lower()
    for key in dict.fromkeys([str(value).strip().lower(), normalize_value(value)]):
        if not key:
            continue
        positions, start = [], 0
        while (i := lowered.find(key, start)) >= 0:
            positions.append((i, i + len(key)))
            start = i + 1
        if positions:
            return positions
    return []
