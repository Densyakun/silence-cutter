from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .detect import CutPlan
from .media import MediaError, PathLike, ProgressCallback, find_ffmpeg


AUDIO_CODEC_BY_SUFFIX = {
    ".wav": "pcm_s16le",
    ".wave": "pcm_s16le",
    ".aiff": "pcm_s24be",
    ".aif": "pcm_s24be",
    ".mp3": "libmp3lame",
    ".flac": "flac",
    ".ogg": "libvorbis",
    ".opus": "libopus",
}


@dataclass(frozen=True)
class RenderSettings:
    """カット後の書き出し設定。再エンコード前提 (フレーム精度を優先)。"""

    video_codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    pix_fmt: str = "yuv420p"
    faststart: bool = True
    extra_output_args: Tuple[str, ...] = field(default_factory=tuple)


def _trim_filter(plan: CutPlan) -> Tuple[str, str, List[str]]:
    """filter_complex 文字列、映像出力ラベル、音声出力ラベル一覧を返す。"""
    has_video = plan.has_video
    has_audio = plan.has_audio
    chains: List[str] = []
    video_labels: List[str] = []
    audio_labels: List[str] = []

    for index, span in enumerate(plan.keep):
        start = f"{span.start:.6f}"
        end = f"{span.end:.6f}"
        if has_video:
            chains.append(
                f"[0:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
            )
            video_labels.append(f"[v{index}]")
        if has_audio:
            chains.append(
                f"[0:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]"
            )
            audio_labels.append(f"[a{index}]")

    count = len(plan.keep)
    if has_video and has_audio:
        inputs = "".join(video_labels[i] + audio_labels[i] for i in range(count))
        chains.append(f"{inputs}concat=n={count}:v=1:a=1[vcat][acat]")
        return ";".join(chains), "[vcat]", ["[acat]"]
    if has_video:
        chains.append(f"{''.join(video_labels)}concat=n={count}:v=1:a=0[vcat]")
        return ";".join(chains), "[vcat]", []
    chains.append(f"{''.join(audio_labels)}concat=n={count}:v=0:a=1[acat]")
    return ";".join(chains), "", ["[acat]"]


def build_command(
    plan: CutPlan,
    output: PathLike,
    settings: Optional[RenderSettings] = None,
    *,
    ffmpeg: Optional[PathLike] = None,
    overwrite: bool = True,
    progress: bool = False,
) -> List[str]:
    """書き出し用の FFmpeg コマンドを組み立てる (実行はしない)。"""
    if not plan.keep:
        raise ValueError("残す区間がありません。しきい値や最小無音長を調整してください。")
    settings = settings or RenderSettings()
    executable = find_ffmpeg(ffmpeg)
    target = Path(output)

    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-y" if overwrite else "-n",
        "-i", plan.source,
    ]
    tail: List[str] = ["-progress", "pipe:1", "-nostats"] if progress else []

    whole_file = (
        len(plan.keep) == 1
        and plan.keep[0].start <= 1e-3
        and plan.keep[0].end >= plan.duration - 1e-3
        and target.suffix.lower() == Path(plan.source).suffix.lower()
    )
    if whole_file:
        command += ["-map", "0", "-c", "copy"]
        if settings.faststart and target.suffix.lower() in (".mp4", ".mov", ".m4v"):
            command += ["-movflags", "+faststart"]
        return command + tail + [str(target)]

    chain, video_out, audio_outs = _trim_filter(plan)
    if not video_out and not audio_outs:
        raise ValueError("映像も音声もない素材は書き出せません。")

    command += ["-filter_complex", chain]
    if video_out:
        command += ["-map", video_out]
    if audio_outs:
        command += ["-map", audio_outs[0]]

    if video_out:
        command += [
            "-c:v", settings.video_codec,
            "-preset", settings.preset,
            "-crf", str(settings.crf),
            "-pix_fmt", settings.pix_fmt,
        ]
    else:
        command += ["-vn"]

    if audio_outs:
        audio_codec = (
            settings.audio_codec if video_out
            else AUDIO_CODEC_BY_SUFFIX.get(target.suffix.lower(), settings.audio_codec)
        )
        command += ["-c:a", audio_codec]
        if not audio_codec.startswith("pcm") and audio_codec != "flac":
            command += ["-b:a", settings.audio_bitrate]
    else:
        command += ["-an"]

    if settings.faststart and target.suffix.lower() in (".mp4", ".mov", ".m4v"):
        command += ["-movflags", "+faststart"]
    command += list(settings.extra_output_args)
    return command + tail + [str(target)]


def _parse_progress(line: str, total: float) -> Optional[float]:
    line = line.strip()
    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
        try:
            seconds = int(line.split("=", 1)[1]) / 1_000_000.0
        except ValueError:
            return None
        return max(0.0, min(0.999, seconds / total)) if total > 0 else None
    if line.startswith("out_time="):
        try:
            hours, minutes, seconds = line.split("=", 1)[1].split(":")
            value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
        return max(0.0, min(0.999, value / total)) if total > 0 else None
    return None


def _write_filter_script(chain: str) -> Path:
    """長い filter_complex をコマンドラインではなくファイルで渡す。

    Windows のコマンドライン上限 (32767 文字) を超えると
    FileNotFoundError: [WinError 206] になるため、常にファイル経由にする。
    """
    handle, name = tempfile.mkstemp(prefix="silence-cut-filter-", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(chain)
    return Path(name)


def render(
    plan: CutPlan,
    output: PathLike,
    settings: Optional[RenderSettings] = None,
    *,
    ffmpeg: Optional[PathLike] = None,
    progress: Optional[ProgressCallback] = None,
    overwrite: bool = True,
) -> Path:
    """カットプランを FFmpeg で書き出す。戻り値は出力パス。"""
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(
        plan, target, settings, ffmpeg=ffmpeg, overwrite=overwrite,
        progress=progress is not None,
    )

    filter_script: Optional[Path] = None
    if "-filter_complex" in command:
        index = command.index("-filter_complex")
        filter_script = _write_filter_script(command[index + 1])
        command[index] = "-filter_complex_script"
        command[index + 1] = str(filter_script)

    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    stderr = ""
    try:
        for line in proc.stdout:
            if progress:
                value = _parse_progress(line, plan.output_duration)
                if value is not None:
                    progress(value)
        proc.wait()
        stderr = proc.stderr.read() if proc.stderr else ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        if filter_script is not None:
            filter_script.unlink(missing_ok=True)

    if proc.returncode != 0:
        detail = stderr.strip().splitlines()
        raise MediaError(
            "書き出しに失敗しました: " + (detail[-1] if detail else f"exit code {proc.returncode}")
        )
    if progress:
        progress(1.0)
    return target


def render_preview(
    plan: CutPlan,
    *,
    ffmpeg: Optional[PathLike] = None,
    progress: Optional[ProgressCallback] = None,
    directory: Optional[PathLike] = None,
) -> Path:
    """低画質・高速設定でプレビュー用ファイルを作る。"""
    folder = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="silence-cut-"))
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(plan.source).stem[:48] or "preview"
    target = folder / f"{stem}_preview.mp4"
    settings = RenderSettings(preset="ultrafast", crf=30, audio_bitrate="128k")
    return render(plan, target, settings, ffmpeg=ffmpeg, progress=progress)


def play_file(path: PathLike) -> bool:
    """ffplay があればそれで、無ければ OS 既定のプレーヤーで開く。"""
    target = Path(path)
    player = shutil.which("ffplay")
    if player:
        creationflags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
        subprocess.Popen(
            [player, "-autoexit", "-loglevel", "error", str(target)],
            creationflags=creationflags,
            close_fds=True,
        )
        return True
    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:  # pragma: no cover - 非 Windows
            launcher = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([launcher, str(target)])
        return True
    except OSError:
        return False
