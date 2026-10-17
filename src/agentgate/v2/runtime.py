"""v2 Gate: 툴 응답마다 IPI detector 라벨을 붙인다. 판정은 Trusted LLM이 한다."""

from __future__ import annotations

ATTACK_MARKER = "<INFORMATION>"   # AgentDojo attack template marker: evaluation label only, never read by policies

from agentgate.gate.runtime import GatedRuntime, TraceEvent
from agentgate.v2.detector.inputs import prepare_input
from agentgate.v2.labels import attach_detection


class DetectorGatedRuntime(GatedRuntime):
    def __init__(self, functions=(), *, detector=None, **kwargs):
        super().__init__(functions, **kwargs)
        self.detector = detector
        self.trace_label_keys = ("detection",) if detector is not None else ()

    def label_response(self, source, event: TraceEvent) -> None:
        if self.detector is None:
            return
        text = prepare_input(getattr(self.detector, "input_format", "raw"), event.raw_output, event.output_text)
        detection = self.detector.detect(text)
        attach_detection(source, detection, text)
        event.labels["detection"] = {"detector": detection.detector, "mode": detection.mode,
                                     "score": round(detection.score, 4), "flagged": detection.flagged,
                                     "suspicious_spans": len(detection.suspicious_spans()),
                                     "latency_ms": round(detection.latency_ms, 1), "cached": detection.cached,
                                     "attack_marker": ATTACK_MARKER in event.output_text}
