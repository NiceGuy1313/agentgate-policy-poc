"""ML 인젝션 판별기 공통 규약.

판별기는 툴 호출이 아니라 **툴 응답 텍스트**를 분류한다(명세서.md 2절). 결과는 응답 전체의 최고 점수와
조각별 점수(의심 구간)이며, GatedRuntime이 이 결과를 해당 응답의 IndexedSource에 라벨로 붙인다.
정책 코드(src/agentgate/policy/)는 이 라벨만 읽고 모델을 직접 부르지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Span:
    """검사한 조각 하나. start/end는 판별기에 넘긴 텍스트 기준 문자 오프셋이다."""

    start: int
    end: int
    score: float


@dataclass
class DetectionResult:
    detector: str                     # e.g. "piguard", "protectai-v2"
    mode: str                         # "full" | "chunks"
    score: float                      # max chunk score (injection probability, 0..1)
    flagged: bool                     # score >= threshold
    threshold: float
    spans: list[Span] = field(default_factory=list)   # every chunk, in text order
    n_chars: int = 0
    latency_ms: float = 0.0
    cached: bool = False

    def suspicious_spans(self) -> list[Span]:
        return [s for s in self.spans if s.score >= self.threshold]

    def covers(self, start: int, end: int) -> float:
        """[start, end) 구간과 겹치는 조각의 최고 점수. 겹치는 조각이 없으면 0."""
        return max((s.score for s in self.spans if s.start < end and start < s.end), default=0.0)

    def as_dict(self) -> dict:
        return asdict(self)


class Detector(Protocol):
    name: str
    threshold: float

    def detect(self, text: str) -> DetectionResult: ...
