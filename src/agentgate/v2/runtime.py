"""v2 Gate: 툴 응답마다 판별기 라벨을 붙이고, 규칙 ④를 적용한다. detector=None이면 v1 Gate와 같다."""

from __future__ import annotations

from agentgate.gate.runtime import GatedRuntime, TraceEvent
from agentgate.v2.labels import attach_detection


class DetectorGatedRuntime(GatedRuntime):
    def __init__(self, functions=(), *, detector=None, **kwargs):
        super().__init__(functions, **kwargs)
        self.detector = detector
        self.trace_label_keys = ("detection",) if detector is not None else ()

    def label_response(self, source, event: TraceEvent) -> None:
        if self.detector is None:
            return
        detection = self.detector.detect(event.output_text)
        attach_detection(source, detection, event.output_text)
        event.labels["detection"] = {"detector": detection.detector, "mode": detection.mode,
                                     "score": round(detection.score, 4), "flagged": detection.flagged,
                                     "suspicious_spans": len(detection.suspicious_spans()),
                                     "latency_ms": round(detection.latency_ms, 1), "cached": detection.cached}

    def finalize_judgement(self, judgement: dict) -> dict:
        # ④: an irreversible call stays with a human when its sources carry a detector warning, even if the
        # judge allowed it
        if (judgement.get("final") == "ALLOW" and judgement.get("detector_warning")
                and judgement.get("reversibility") == "irreversible"):
            return {**judgement, "final": "HUMAN", "verdict": "BLOCK", "human": True, "rule4": True}
        return judgement
