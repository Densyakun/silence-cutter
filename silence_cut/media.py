from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from operator import mul
from pathlib import Path
from typing import Callable, List, Optional, Union

from .settings import AnalysisSettings

PathLike = Union[str, Path]
ProgressCallback = Callable[[float], None]

FFMPEG_FALLBACKS = (
    "ffmpeg",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
)
FFPROBE_FALLBACKS = (
    "ffprobe",
    r"C:\ffmpeg\bin\ffprobe.exe",
    "/usr/local/bin/ffprobe",
    "/opt/homebrew/bin/ffprobe",
)

SILENCE_FLOOR_DB = -120.0


class MediaError(RuntimeError):
    """FFmpeg / 入力ファイルに関するエラー。"""


def find_binary(name: str, explicit: Optional[PathLike] = None,
                fallbacks: Optional[tuple] = None) -> str:
    candidates = []
    if explicit:
        candidates.append(str(explicit))
    candidates.extend(fallbacks or (name,))
    for candidate in candidates:
        if not candidate:
            continue
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
        found = shutil.which(candidate)
        if found:
            return found
    raise MediaError(
        f"{name} が見つかりません。FFmpeg をインストールして PATH に追加するか、"
        f"実行時のオプションで場所を指定してください。"
    )


def find_ffmpeg(explicit: Optional[PathLike] = None) -> str:
    return find_binary("ffmpeg", explicit, FFMPEG_FALLBACKS)


def find_ffprobe(explicit: Optional[PathLike] = None) -> str:
    return find_binary("ffprobe", explicit, FFPROBE_FALLBACKS)


def _run_json(cmd: List[str]) -> dict:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise MediaError(detail[-1] if detail else "ffprobe に失敗しました")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:  # pragma: no cover - 破損出力は異常
        raise MediaError(f"ffprobe の出力を解析できませんでした: {exc}") from exc


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_rate(value) -> float:
    if not value:
        return 0.0
    text = str(value)
    if "/" in text:
        num, _, den = text.partition("/")
        denominator = _to_float(den)
        return _to_float(num) / denominator if denominator else 0.0
    return _to_float(text)


@dataclass
class MediaInfo:
    path: str
    duration: float
    fps: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    audio_sample_rate: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "fps": self.fps,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "width": self.width,
            "height": self.height,
            "audio_sample_rate": self.audio_sample_rate,
        }


def probe(path: PathLike, ffprobe: Optional[PathLike] = None) -> MediaInfo:
    source = Path(path)
    if not source.is_file():
        raise MediaError(f"入力ファイルが見つかりません: {source}")

    data = _run_json([
        find_ffprobe(ffprobe), "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams",
        str(source),
    ])
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next(
        (s for s in streams
         if s.get("codec_type") == "video"
         and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    durations = [_to_float(fmt.get("duration"))]
    for stream in streams:
        durations.append(_to_float(stream.get("duration")))
    duration = max(durations) if durations else 0.0
    if duration <= 0:
        raise MediaError("素材の長さを取得できませんでした。ファイルが壊れていないか確認してください。")

    fps = 0.0
    if video:
        fps = _parse_rate(video.get("avg_frame_rate"))
        if fps <= 0:
            fps = _parse_rate(video.get("r_frame_rate"))

    return MediaInfo(
        path=str(source),
        duration=duration,
        fps=round(fps, 6),
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(_to_float((video or {}).get("width"))),
        height=int(_to_float((video or {}).get("height"))),
        audio_sample_rate=int(_to_float((audio or {}).get("sample_rate"))),
    )


@dataclass
class AudioLevels:
    """フレームごとの音量と発話判定用の特徴量。

    ``zcr`` (ゼロ交差率) と ``low_ratio`` (1 kHz 以下の低域エネルギー比) は
    発話区間検出 (無声カット) を使うときだけ計算される。
    """

    sample_rate: int
    frame_ms: float
    db: List[float]
    duration: float
    zcr: List[float] = field(default_factory=list)
    low_ratio: List[float] = field(default_factory=list)

    @property
    def frame_seconds(self) -> float:
        return self.frame_ms / 1000.0

    @property
    def frame_count(self) -> int:
        return len(self.db)

    @property
    def has_spectral_features(self) -> bool:
        return bool(self.zcr) and bool(self.low_ratio)

    def times(self) -> List[float]:
        step = self.frame_seconds
        return [i * step for i in range(len(self.db))]

    def envelope(self, buckets: int, floor_db: float = -80.0) -> List[float]:
        """表示用に、指定個数のビンへ最大値を間引いた音量列を返す。"""
        if buckets <= 0 or not self.db:
            return []
        if buckets >= len(self.db):
            return list(self.db)
        result = []
        total = len(self.db)
        for i in range(buckets):
            start = i * total // buckets
            end = max(start + 1, (i + 1) * total // buckets)
            result.append(max(self.db[start:end]))
        return [max(floor_db, value) for value in result]

    def to_dict(self) -> dict:
        data = {
            "sample_rate": self.sample_rate,
            "frame_ms": self.frame_ms,
            "duration": self.duration,
            "db": [round(value, 2) for value in self.db],
        }
        if self.zcr:
            data["zcr"] = [round(value, 4) for value in self.zcr]
        if self.low_ratio:
            data["low_ratio"] = [round(value, 4) for value in self.low_ratio]
        return data


def _dot(values: array.array) -> float:
    sumprod = getattr(math, "sumprod", None)
    if sumprod is not None:  # Python 3.12+
        return sumprod(values, values)
    return sum(map(mul, values, values))


def measure_levels(
    path: PathLike,
    settings: AnalysisSettings,
    *,
    media: Optional[MediaInfo] = None,
    ffmpeg: Optional[PathLike] = None,
    progress: Optional[ProgressCallback] = None,
    sample_rate: int = 16000,
    features: bool = False,
) -> AudioLevels:
    """FFmpeg でモノラル PCM にデコードしながらフレームの特徴量を計算する。

    音声はファイルにも外部にも送らない (ローカル処理のみ)。
    ``features=True`` で ZCR と低域エネルギー比も計算する (発話区間検出用)。
    """
    settings = settings.validated()
    source = Path(path)
    info = media or probe(source)
    if not info.has_audio:
        raise MediaError(f"音声ストリームが見つかりません: {source}")

    hop = max(1, int(round(sample_rate * settings.frame_ms / 1000.0)))
    frame_bytes = hop * 4
    read_bytes = frame_bytes * 256

    cmd = [
        find_ffmpeg(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", str(sample_rate),
        "-f", "f32le", "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
    )
    db: List[float] = []
    zcr: List[float] = []
    low_ratio: List[float] = []
    log10 = math.log10
    pending = b""
    stderr = b""
    lowpass_alpha = 1.0 - math.exp(-2.0 * math.pi * 1000.0 / sample_rate)
    lowpass_inverse = 1.0 - lowpass_alpha
    lowpass_state = 0.0
    previous_sample = 1.0
    try:
        assert proc.stdout is not None
        while True:
            block = proc.stdout.read(read_bytes)
            if not block:
                break
            pending += block
            complete = len(pending) - len(pending) % frame_bytes
            if complete <= 0:
                continue
            samples = array.array("f")
            samples.frombytes(pending[:complete])
            if sys.byteorder != "little":
                samples.byteswap()
            for start in range(0, len(samples), hop):
                chunk = samples[start:start + hop]
                length = len(chunk)
                if length == 0:  # pragma: no cover
                    continue
                if features:
                    total = 0.0
                    low = 0.0
                    crossings = 0
                    previous = previous_sample
                    for value in chunk:
                        total += value * value
                        lowpass_state = (
                            lowpass_alpha * value + lowpass_inverse * lowpass_state
                        )
                        low += lowpass_state * lowpass_state
                        if (value < 0.0) != (previous < 0.0):
                            crossings += 1
                        previous = value
                    previous_sample = previous
                    mean_square = total / length
                    zcr.append(crossings / length)
                    low_ratio.append(
                        min(1.0, low / total) if total > 1e-12 else 0.0
                    )
                else:
                    mean_square = _dot(chunk) / length
                value = 10.0 * log10(mean_square + 1e-12) if mean_square > 0 else SILENCE_FLOOR_DB
                db.append(max(SILENCE_FLOOR_DB, value))
            pending = pending[complete:]
            if progress and info.duration > 0:
                progress(min(0.99, len(db) * settings.frame_ms / 1000.0 / info.duration))
        proc.wait()
        stderr = proc.stderr.read() if proc.stderr else b""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(
            "音声のデコードに失敗しました: " + (detail[-1] if detail else f"exit code {proc.returncode}")
        )

    duration = len(db) * settings.frame_ms / 1000.0
    if duration <= 0:
        raise MediaError(f"音声を読み取れませんでした: {source}")
    if progress:
        progress(1.0)
    return AudioLevels(
        sample_rate=sample_rate,
        frame_ms=settings.frame_ms,
        db=db,
        duration=duration,
        zcr=zcr,
        low_ratio=low_ratio,
    )
