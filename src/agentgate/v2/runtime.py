"""v2 Gate: 툴 응답마다 판별기 라벨을 붙인다. 규칙 ④는 config/v2/policies.yaml의 v2.rule4가 true일 때만 쓴다."""

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

    def finalize_judgement(self, judgement: dict) -> dict:
        # ④: an irreversible call stays with a human when its sources carry a detector warning, even if the
        # judge allowed it (off by default: it turned most judge ALLOWs on flagged sources into BLOCK)
        if (self.config.get("v2") or {}).get("rule4") and (judgement.get("final") == "ALLOW" and judgement.get("detector_warning")
                and judgement.get("reversibility") == "irreversible"):
            return {**judgement, "final": "HUMAN", "verdict": "BLOCK", "human": True, "rule4": True}
        return judgement
