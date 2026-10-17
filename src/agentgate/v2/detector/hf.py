"""Hugging Face 분류 모델 기반 판별기(Horizon-Labs prompt-injection-guard, PIGuard).

모델 이름·리비전·레이블·임계값은 config/v2/detector.yaml에서 읽는다. transformers/torch는 이 모듈을 쓸 때만
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
WindowScoreFn = Callable[[str], list[tuple[int, int, float]]]
"""text -> (start char, end char, injection probability) per token window."""


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


def load_hf_window_scorer(model_id: str, revision: str | None, injection_label: str, trust_remote_code: bool,
                          window: int = 2048, stride: int = 512, batch_size: int = 8) -> WindowScoreFn:
    """Token windows as in the Horizon-Labs model card: `window` tokens, `stride` overlapping tokens, max over windows.

    Each window keeps its character span (from the tokenizer offsets) so a value can be located in its window.
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(model_id, revision=revision,
                                                               trust_remote_code=trust_remote_code).eval()
    labels = {v.lower(): int(k) for k, v in model.config.id2label.items()}
    positive = labels[injection_label.lower()]

    @torch.no_grad()
    def score(text: str) -> list[tuple[int, int, float]]:
        enc = tokenizer(text, truncation=True, max_length=window, stride=stride, return_overflowing_tokens=True,
                        return_offsets_mapping=True, padding=True, return_tensors="pt")
        offsets = enc.pop("offset_mapping").tolist()
        enc.pop("overflow_to_sample_mapping", None)
        enc.pop("token_type_ids", None)
        probs = []
        for i in range(0, enc["input_ids"].shape[0], batch_size):
            batch = {k: v[i : i + batch_size] for k, v in enc.items()}
            probs += torch.softmax(model(**batch).logits.float(), -1)[:, positive].tolist()
        windows = []
        for spans, p in zip(offsets, probs):
            chars = [(a, b) for a, b in spans if b > a]   # special and padding tokens have (0, 0)
            windows.append((chars[0][0], chars[-1][1], float(p)) if chars else (0, len(text), float(p)))
        return windows

    return score


class HFDetector:
    def __init__(self, name: str, scorer: ScoreFn | None, threshold: float, mode: str = "chunks",
                 chunk_chars: int = 1000, overlap_chars: int = 200, cache_key: str | None = None,
                 use_cache: bool = True, window_scorer: WindowScoreFn | None = None):
        if mode not in ("full", "chunks", "windows"):
            raise ValueError("mode must be full | chunks | windows")
        if (mode == "windows") != (window_scorer is not None):
            raise ValueError("mode windows needs a window_scorer (and only that mode uses one)")
        self.name, self.scorer, self.threshold, self.mode = name, scorer, threshold, mode
        self.window_scorer = window_scorer
        self.input_format = "raw"   # set by make_detector from config/v2/detector.yaml (agentgate.v2.detector.inputs)
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
        if self.mode == "windows":
            spans = [Span(a, b, s) for a, b, s in self.window_scorer(text)] if text else [Span(0, 0, 0.0)]
            latency = (time.perf_counter() - start) * 1000
            if self.use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"spans": [[s.start, s.end, s.score] for s in spans]}))
            return self._result(spans, len(text), latency, cached=False)
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
