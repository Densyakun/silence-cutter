from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .media import AudioLevels
from .settings import AnalysisSettings

# この長さ以下の隙間はカットせず発話側に吸収する (秒)
MERGE_GAP = 0.02


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def clamped(self, duration: float) -> "Span":
        return Span(max(0.0, min(self.start, duration)), max(0.0, min(self.end, duration)))

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 6),
            "end": round(self.end, 6),
            "duration": round(self.duration, 6),
        }


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


def _otsu_threshold(values: Sequence[float], bins: int = 512) -> float:
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


def resolve_threshold(db: Sequence[float], settings: AnalysisSettings) -> float:
    """固定しきい値、またはノイズフロアから推定したしきい値を返す。

    自動推定では「ノイズフロア + マージン」と「2 クラス分離 (大津)」の
    低い方を採用する。発話が大半の素材でも全カットにならないための保険。
    """
    if not settings.adaptive or not db:
        return settings.threshold_db
    noise = percentile(db, settings.adaptive_percentile / 100.0)
    estimated = noise + settings.adaptive_margin_db
    separated = _otsu_threshold(db)
    threshold = min(estimated, separated)
    return max(-80.0, min(threshold, -20.0))


def _mask_runs(mask: List[bool]):
    if not mask:
        return
    value = mask[0]
    start = 0
    for index in range(1, len(mask)):
        if mask[index] != value:
            yield value, start, index
            value = mask[index]
            start = index
    yield value, start, len(mask)


def _remove_short_runs(mask: List[bool], min_frames: int, run_value: bool) -> List[bool]:
    if min_frames <= 1:
        return mask
    result = mask[:]
    for value, start, end in _mask_runs(mask):
        if value == run_value and (end - start) < min_frames:
            result[start:end] = [not run_value] * (end - start)
    return result


def _spans_from_mask(mask: List[bool], frame_seconds: float, duration: float) -> List[Span]:
    spans: List[Span] = []
    for value, start, end in _mask_runs(mask):
        if not value:
            continue
        spans.append(Span(start * frame_seconds, end * frame_seconds).clamped(duration))
    return [span for span in spans if span.duration > 0]


def _merge_spans(spans: List[Span], gap: float, duration: float,
                 pad_start: float, pad_end: float) -> List[Span]:
    padded = [
        Span(span.start - pad_start, span.end + pad_end).clamped(duration)
        for span in spans
    ]
    padded.sort(key=lambda span: span.start)
    merged: List[Span] = []
    for span in padded:
        if span.duration <= 0:
            continue
        if merged and span.start - merged[-1].end <= max(gap, 1e-6):
            last = merged[-1]
            merged[-1] = Span(last.start, max(last.end, span.end))
        else:
            merged.append(span)
    return merged


def _complement(keep: List[Span], duration: float, min_gap: float) -> List[Span]:
    cut: List[Span] = []
    cursor = 0.0
    for span in keep:
        if span.start - cursor > min_gap:
            cut.append(Span(cursor, span.start))
        cursor = max(cursor, span.end)
    if duration - cursor > min_gap:
        cut.append(Span(cursor, duration))
    return [span for span in cut if span.duration > 0]


@dataclass
class CutPlan:
    """解析結果。プラグイン/単体アプリの双方が受け取る共通フォーマット。"""

    source: str
    duration: float
    keep: List[Span] = field(default_factory=list)
    cut: List[Span] = field(default_factory=list)
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    threshold_db: float = -45.0
    fps: float = 30.0
    has_video: bool = True
    has_audio: bool = True

    @property
    def speech_duration(self) -> float:
        return sum(span.duration for span in self.keep)

    @property
    def silence_duration(self) -> float:
        return sum(span.duration for span in self.cut)

    @property
    def output_duration(self) -> float:
        return self.speech_duration

    @property
    def removed_ratio(self) -> float:
        return self.silence_duration / self.duration if self.duration > 0 else 0.0

    @property
    def cut_count(self) -> int:
        return len(self.cut)

    def timeline(self) -> List[dict]:
        """カット後のタイムライン上の位置に対応づけた区間リスト。"""
        records = []
        cursor = 0.0
        for span in self.keep:
            length = span.duration
            records.append({
                "source_start": round(span.start, 6),
                "source_end": round(span.end, 6),
                "record_start": round(cursor, 6),
                "record_end": round(cursor + length, 6),
            })
            cursor += length
        return records

    def summary(self) -> Dict[str, object]:
        return {
            "duration": round(self.duration, 3),
            "output_duration": round(self.output_duration, 3),
            "speech_duration": round(self.speech_duration, 3),
            "silence_duration": round(self.silence_duration, 3),
            "removed_ratio": round(self.removed_ratio, 4),
            "keep_segments": len(self.keep),
            "cut_segments": len(self.cut),
            "threshold_db": round(self.threshold_db, 2),
        }

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "source": {
                "path": self.source,
                "duration": round(self.duration, 6),
                "fps": self.fps,
                "has_video": self.has_video,
                "has_audio": self.has_audio,
            },
            "settings": self.settings.to_dict(),
            "threshold_db": round(self.threshold_db, 4),
            "summary": self.summary(),
            "keep": [span.to_dict() for span in self.keep],
            "cut": [span.to_dict() for span in self.cut],
            "timeline": self.timeline(),
        }

    def describe(self) -> str:
        summary = self.summary()
        minutes, seconds = divmod(self.output_duration, 60)
        cut_minutes, cut_seconds = divmod(self.silence_duration, 60)
        return (
            f"無音 {summary['cut_segments']} 箇所 / "
            f"{int(cut_minutes)}:{cut_seconds:04.1f} を除去 "
            f"({self.removed_ratio * 100:.0f}%) → "
            f"{int(minutes)}:{seconds:04.1f}"
        )


def build_plan(
    levels: AudioLevels,
    settings: AnalysisSettings,
    *,
    duration: Optional[float] = None,
    source: str = "",
    fps: float = 30.0,
    has_video: bool = True,
    has_audio: bool = True,
    activity: Optional[Sequence[bool]] = None,
) -> CutPlan:
    """フレーム音量列からカットプランを作る (FFmpeg 非依存の純粋処理)。

    ``activity`` を渡すと音量しきい値の代わりにその発話判定を使う。
    AI の VAD など外部検出器を差し込む場合の拡張点。
    """
    settings = settings.validated()
    duration = float(duration if duration is not None else levels.duration)
    db = levels.db
    threshold = resolve_threshold(db, settings)
    frame_seconds = levels.frame_ms / 1000.0

    if not db or duration <= 0:
        return CutPlan(
            source=source, duration=max(0.0, duration), settings=settings,
            threshold_db=threshold, fps=fps, has_video=has_video, has_audio=has_audio,
        )

    if activity is not None:
        mask = [bool(value) for value in activity]
        if len(mask) != len(db):
            raise ValueError(
                "activity のフレーム数が音声解析結果と一致しません "
                f"({len(mask)} != {len(db)})"
            )
    else:
        mask = [value >= threshold for value in db]
    min_speech_frames = int(math.ceil(settings.min_speech / frame_seconds - 1e-9))
    min_silence_frames = int(math.ceil(settings.min_silence / frame_seconds - 1e-9))

    # 短すぎる発話 (クリック音など) を無音へ、短すぎる無音を発話へ寄せる
    mask = _remove_short_runs(mask, min_speech_frames, True)
    mask = _remove_short_runs(mask, min_silence_frames, False)

    keep = _merge_spans(
        _spans_from_mask(mask, frame_seconds, duration),
        MERGE_GAP, duration,
        max(0.0, settings.pad_start), max(0.0, settings.pad_end),
    )
    if (len(keep) == 1 and keep[0].start <= 0.0
            and keep[0].end >= duration):
        keep = [Span(0.0, duration)]

    min_cut_gap = min(0.05, max(0.0, settings.min_silence))
    cut = _complement(keep, duration, min_cut_gap)

    return CutPlan(
        source=source,
        duration=duration,
        keep=keep,
        cut=cut,
        settings=settings,
        threshold_db=threshold,
        fps=fps,
        has_video=has_video,
        has_audio=has_audio,
    )
