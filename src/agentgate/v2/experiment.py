"""v2 실험 조건(C5) 런타임 조립. 판별기 모델은 프로세스당 한 번 읽고 스레드 사이에 lock으로 공유한다."""

from __future__ import annotations

import os
import threading

from agentgate.v2 import make_engine
from agentgate.v2.runtime import DetectorGatedRuntime

DEFAULT_DETECTOR = ("piguard", "chunks")
_DETECTORS: dict = {}
_LOCK = threading.Lock()


class _SharedDetector:
    def __init__(self, name: str, mode: str):
        from agentgate.v2.detector import make_detector
        self._detector = make_detector(name, mode)
        self._lock = threading.Lock()

    def detect(self, text: str):
        with self._lock:
            return self._detector.detect(text)


def shared_detector(name: str, mode: str):
    with _LOCK:
        if (name, mode) not in _DETECTORS:
            _DETECTORS[(name, mode)] = _SharedDetector(name, mode)
        return _DETECTORS[(name, mode)]


def build(cond, *, suite_name: str, prompt: str, escalate: str, disabled=None, stats=None):
    """Detector: AGENTGATE_V2_DETECTOR=name[:mode] (default piguard:chunks), "none" = no detector (v1-equivalent P10)."""
    choice = os.environ.get("AGENTGATE_V2_DETECTOR") or ":".join(DEFAULT_DETECTOR)
    detector = None
    if choice != "none":
        name, _, mode = choice.partition(":")
        detector = shared_detector(name, mode or DEFAULT_DETECTOR[1])
    kwargs = {"engine": make_engine(disabled), "detector": detector}
    if escalate == "judge":
        from agentgate.v2.judge import TrustedJudgeV2, as_runtime_judge
        kwargs["judge"] = as_runtime_judge(TrustedJudgeV2(), prompt)
    return DetectorGatedRuntime, kwargs
