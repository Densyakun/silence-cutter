from __future__ import annotations

import math
from typing import Sequence


def percentile(values: Sequence[float], ratio: float) -> float:
    """線形補間つきパーセンタイル (numpy 非依存)。ratio は 0.0〜1.0。"""
    if not values:
        raise ValueError("値が空です")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, ratio))
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def otsu_threshold(values: Sequence[float], bins: int = 512) -> float:
    """1 次元の 2 クラス分離（大津の手法）。しきい値の候補を返す。"""
    if not values:
        return -45.0
    low = min(values)
    high = max(values)
    if high - low < 1.0:
        return low
    width = (high - low) / bins
    histogram = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        histogram[index] += 1

    total = len(values)
    weighted_sum = sum(
        (low + (index + 0.5) * width) * count
        for index, count in enumerate(histogram)
    )
    weight_background = 0
    sum_background = 0.0
    best_variance = -1.0
    best_threshold = low
    for index, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += (low + (index + 0.5) * width) * count
        mean_background = sum_background / weight_background
        mean_foreground = (weighted_sum - sum_background) / weight_foreground
        variance = (
            weight_background * weight_foreground
            * (mean_background - mean_foreground) ** 2
        )
        if variance > best_variance:
            best_variance = variance
            best_threshold = low + (index + 1) * width
    return best_threshold
