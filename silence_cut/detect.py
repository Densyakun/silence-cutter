from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .media import AudioLevels
from .settings import AnalysisSettings
from .thresholds import resolve_threshold
from .vad import detect_speech

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
    noise_db: Optional[float] = None

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
        data: Dict[str, object] = {
            "mode": self.settings.detection,
            "duration": round(self.duration, 3),
            "output_duration": round(self.output_duration, 3),
            "speech_duration": round(self.speech_duration, 3),
            "silence_duration": round(self.silence_duration, 3),
            "removed_ratio": round(self.removed_ratio, 4),
            "keep_segments": len(self.keep),
            "cut_segments": len(self.cut),
            "threshold_db": round(self.threshold_db, 2),
        }
        if self.noise_db is not None:
            data["noise_db"] = round(self.noise_db, 2)
        return data

    def to_dict(self) -> dict:
        return {
            "version": 2,
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
        label = "非発話" if self.settings.detection == "speech" else "無音"
        return (
            f"{label} {summary['cut_segments']} 箇所 / "
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
    """フレームの特徴量からカットプランを作る (FFmpeg 非依存の純粋処理)。

    ``settings.detection`` が ``"speech"`` のときは発話区間検出 (無声カット)、
    ``"silence"`` のときは音量しきい値による無音判定を行う。
    ``activity`` を渡すとその判定を最優先で使う (AI の VAD などを差し込む拡張点)。
    """
    settings = settings.validated()
    duration = float(duration if duration is not None else levels.duration)
    db = levels.db
    frame_seconds = levels.frame_ms / 1000.0

    if not db or duration <= 0:
        return CutPlan(
            source=source, duration=max(0.0, duration), settings=settings,
            threshold_db=settings.threshold_db, fps=fps,
            has_video=has_video, has_audio=has_audio,
        )

    if activity is not None:
        mask = [bool(value) for value in activity]
        if len(mask) != len(db):
            raise ValueError(
                "activity のフレーム数が音声解析結果と一致しません "
                f"({len(mask)} != {len(db)})"
            )
        threshold = resolve_threshold(db, settings)
        noise_db = None
    elif settings.detection == "speech":
        speech = detect_speech(levels, settings)
        mask = speech.mask
        threshold = speech.threshold_db
        noise_db = speech.noise_db
    else:
        threshold = resolve_threshold(db, settings)
        mask = [value >= threshold for value in db]
        noise_db = None

    min_speech_frames = int(math.ceil(settings.min_speech / frame_seconds - 1e-9))
    min_silence_frames = int(math.ceil(settings.min_silence / frame_seconds - 1e-9))

    # 短すぎる発話 (クリック音など) を非発話へ、短すぎる途切れを発話へ寄せる
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
        noise_db=noise_db,
    )
