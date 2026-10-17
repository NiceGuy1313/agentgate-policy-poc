"""Hugging Face 분류 모델 기반 판별기(PIGuard, ProtectAI DeBERTa v2).

모델 이름·리비전·레이블·임계값은 config/detector.yaml에서 읽는다. transformers/torch는 이 모듈을 쓸 때만
import하므로 판별기를 쓰지 않는 기존 경로(pytest, replay v1)는 추가 의존성 없이 돈다.
같은 입력의 점수는 data/v2/cache/detector/<model>/에 저장해 재실행 결과를 고정한다.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from agentgate.config import PROJECT_ROOT
from agentgate.v2.detector.base import DetectionResult, Span
from agentgate.v2.detector.chunking import split_chunks

CACHE_DIR = PROJECT_ROOT / "data" / "v2" / "cache" / "detector"

ScoreFn = Callable[[list[str]], list[float]]
"""texts -> injection probability per text."""


def load_hf_scorer(model_id: str, revision: str | None, injection_label: str, trust_remote_code: bool,
                   max_length: int = 512, batch_size: int = 16, device: str | None = None) -> ScoreFn:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(model_id, revision=revision,
                                                               trust_remote_code=trust_remote_code)
    classifier = pipeline("text-classification", model=model, tokenizer=tokenizer, truncation=True,
                          max_length=max_length, top_k=None,
                          device=device if device is not None else (0 if torch.cuda.is_available() else -1))

    def score(texts: list[str]) -> list[float]:
        outputs = classifier(texts, batch_size=batch_size)
        scores = []
        for labels in outputs:
            by_label = {item["label"].lower(): item["score"] for item in labels}
            scores.append(by_label.get(injection_label.lower(), 0.0))
        return scores

    return score


class HFDetector:
    def __init__(self, name: str, scorer: ScoreFn, threshold: float, mode: str = "chunks",
                 chunk_chars: int = 1000, overlap_chars: int = 200, cache_key: str | None = None,
                 use_cache: bool = True):
        if mode not in ("full", "chunks"):
            raise ValueError("mode must be full | chunks")
        self.name, self.scorer, self.threshold, self.mode = name, scorer, threshold, mode
        self.chunk_chars, self.overlap_chars = chunk_chars, overlap_chars
        self.cache_key = cache_key or name
        self.use_cache = use_cache

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha256(
            f"{self.cache_key}|{self.mode}|{self.chunk_chars}|{self.overlap_chars}|{text}".encode()).hexdigest()[:24]
        return CACHE_DIR / self.cache_key.replace("/", "_").replace(":", "_") / f"{key}.json"

    def detect(self, text: str) -> DetectionResult:
        path = self._cache_path(text)
        if self.use_cache and path.exists():
            spans = [Span(*s) for s in json.loads(path.read_text())["spans"]]
            return self._result(spans, len(text), 0.0, cached=True)
        start = time.perf_counter()
        if self.mode == "full" or not text:
            # model-side truncation at max_length tokens: the OSS default behaviour (baseline)
            bounds = [(0, len(text))]
        else:
            bounds = split_chunks(text, self.chunk_chars, self.overlap_chars)
        scores = self.scorer([text[a:b] for a, b in bounds]) if text else [0.0]
        spans = [Span(a, b, float(s)) for (a, b), s in zip(bounds, scores)]
        latency = (time.perf_counter() - start) * 1000
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"spans": [[s.start, s.end, s.score] for s in spans]}))
        return self._result(spans, len(text), latency, cached=False)

    def _result(self, spans: list[Span], n_chars: int, latency_ms: float, cached: bool) -> DetectionResult:
        top = max((s.score for s in spans), default=0.0)
        return DetectionResult(self.name, self.mode, top, top >= self.threshold, self.threshold, spans,
                               n_chars, latency_ms, cached)
