"""v2 실험 조건(C5) 런타임 조립. 판별기 모델은 프로세스당 한 번 읽고 스레드 사이에 lock으로 공유한다."""

from __future__ import annotations

import os
import threading

from agentgate.v2 import make_engine
from agentgate.v2.runtime import DetectorGatedRuntime

_DETECTORS: dict = {}
_LOCK = threading.Lock()


class _SharedDetector:
    def __init__(self, name: str | None, mode: str | None):
        from agentgate.v2.detector import make_detector
        self._detector = make_detector(name, mode)
        self._lock = threading.Lock()

    @property
    def input_format(self) -> str:
        return self._detector.input_format

    def detect(self, text: str):
        with self._lock:
            return self._detector.detect(text)


def shared_detector(name: str | None = None, mode: str | None = None):
    with _LOCK:
        if (name, mode) not in _DETECTORS:
            _DETECTORS[(name, mode)] = _SharedDetector(name, mode)
        return _DETECTORS[(name, mode)]


def build(cond, *, suite_name: str, prompt: str, escalate: str, disabled=None, stats=None):
    """Detector: the condition's model (C5 horizon-base, C5p piguard). AGENTGATE_V2_DETECTOR=name[:mode] overrides it,
    "none" = no detector (P10 never fires)."""
    choice = os.environ.get("AGENTGATE_V2_DETECTOR") or (cond.detector or "")
    detector = None
    if choice != "none":
        name, _, mode = choice.partition(":")
        detector = shared_detector(name or None, mode or None)
    kwargs = {"engine": make_engine(disabled), "detector": detector}
    if escalate == "judge":
        from agentgate.v2.judge import TrustedJudgeV2, as_runtime_judge
        kwargs["judge"] = as_runtime_judge(TrustedJudgeV2(), prompt)
    return DetectorGatedRuntime, kwargs
