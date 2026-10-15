"""툴 응답용 ML 인젝션 판별기(명세서.md). 모델 로딩은 `make_detector`를 부를 때만 일어난다."""

from __future__ import annotations

import yaml

from agentgate.config import PROJECT_ROOT
from agentgate.detector.base import DetectionResult, Detector, Span
from agentgate.detector.hf import HFDetector, load_hf_scorer

DETECTOR_CONFIG_PATH = PROJECT_ROOT / "config" / "detector.yaml"


def load_detector_config() -> dict:
    return yaml.safe_load(DETECTOR_CONFIG_PATH.read_text())


def make_detector(name: str | None = None, mode: str | None = None, threshold: float | None = None) -> HFDetector:
    """config/detector.yaml의 모델 항목으로 판별기를 만든다. name/mode/threshold는 설정값을 덮어쓴다."""
    config = load_detector_config()
    name = name or config["default"]
    model = config["models"][name]
    chunking = config["chunking"]
    scorer = load_hf_scorer(model["hf_id"], model.get("revision"), model["injection_label"],
                            model.get("trust_remote_code", False), model.get("max_length", 512))
    return HFDetector(name, scorer, threshold if threshold is not None else model["threshold"],
                      mode or chunking["mode"], chunking["chunk_chars"], chunking["overlap_chars"],
                      cache_key=f"{name}@{model.get('revision') or 'main'}")


__all__ = ["DetectionResult", "Detector", "HFDetector", "Span", "load_detector_config", "make_detector"]
