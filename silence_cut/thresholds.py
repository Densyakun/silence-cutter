from __future__ import annotations

from typing import Sequence

from .settings import AnalysisSettings
from .stats import otsu_threshold, percentile


def resolve_threshold(db: Sequence[float], settings: AnalysisSettings) -> float:
    """固定しきい値、またはノイズフロアから推定したしきい値を返す。

    自動推定では「ノイズフロア + マージン」と「2 クラス分離 (大津)」の
    低い方を採用する。発話が大半の素材でも全カットにならないための保険。
    """
    if not settings.adaptive or not db:
        return settings.threshold_db
    noise = percentile(db, settings.adaptive_percentile / 100.0)
    estimated = noise + settings.adaptive_margin_db
    separated = otsu_threshold(db)
    threshold = min(estimated, separated)
    return max(-80.0, min(threshold, -20.0))
