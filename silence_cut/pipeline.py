from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .detect import CutPlan, build_plan
from .media import (
    AudioLevels,
    MediaInfo,
    PathLike,
    ProgressCallback,
    measure_levels,
    probe,
)
from .settings import AnalysisSettings


@dataclass
class AnalysisResult:
    media: MediaInfo
    levels: AudioLevels
    plan: CutPlan

    def to_dict(self) -> dict:
        data = self.plan.to_dict()
        data["source"].update(self.media.to_dict())
        return data


def analyze_file(
    path: PathLike,
    settings: Optional[AnalysisSettings] = None,
    *,
    ffmpeg: Optional[PathLike] = None,
    ffprobe: Optional[PathLike] = None,
    progress: Optional[ProgressCallback] = None,
) -> AnalysisResult:
    """1 ファイルを解析してカットプランを返す。"""
    settings = (settings or AnalysisSettings()).validated()
    source = Path(path)
    media = probe(source, ffprobe=ffprobe)
    levels = measure_levels(
        source, settings, media=media, ffmpeg=ffmpeg, progress=progress,
    )
    plan = build_plan(
        levels,
        settings,
        duration=media.duration,
        source=str(source),
        fps=media.fps or 30.0,
        has_video=media.has_video,
        has_audio=media.has_audio,
    )
    return AnalysisResult(media=media, levels=levels, plan=plan)
