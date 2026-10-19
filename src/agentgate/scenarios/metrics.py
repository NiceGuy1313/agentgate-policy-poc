"""재생·live 결과 파일의 지표 helper: Wilson 구간, exact McNemar, injection 라벨(summarize·significance·replay_contrast 스크립트가 공유)."""

from __future__ import annotations

import math

import yaml

from agentgate.config import PROJECT_ROOT


def load_injection_labels() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "data" / "agentdojo" / "benchmark" / "injection_labels.yaml").read_text())


def wilson(successes: int, trials: int, z: float = 1.959964) -> tuple[float, float] | None:
    if trials == 0:
        return None
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    half_width = z * math.sqrt(proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)) / denominator
    return round(max(0.0, center - half_width), 4), round(min(1.0, center + half_width), 4)


def mcnemar_exact(c0_only: int, condition_only: int) -> float:
    """Two-sided exact McNemar p (binomial on the discordant pairs)."""
    discordant = c0_only + condition_only
    if discordant == 0:
        return 1.0
    smaller = min(c0_only, condition_only)
    return min(1.0, 2 * sum(math.comb(discordant, i) for i in range(smaller + 1)) / 2 ** discordant)
