from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional, Union

from .detect import CutPlan

PathLike = Union[str, Path]


def to_json(plan: CutPlan, *, indent: int = 2) -> str:
    return json.dumps(plan.to_dict(), ensure_ascii=False, indent=indent)


def write_json(plan: CutPlan, path: PathLike, *, indent: int = 2) -> Path:
    target = Path(path)
    target.write_text(to_json(plan, indent=indent) + "\n", encoding="utf-8")
    return target


def to_csv(plan: CutPlan) -> str:
    """編集ソフトのスクリプトや表計算から読みやすい一覧。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "index", "action", "source_start", "source_end", "duration",
        "record_start", "record_end",
    ])
    index = 0
    for span, record in zip(plan.keep, plan.timeline()):
        index += 1
        writer.writerow([
            index, "keep", f"{span.start:.3f}", f"{span.end:.3f}",
            f"{span.duration:.3f}",
            f"{record['record_start']:.3f}", f"{record['record_end']:.3f}",
        ])
    for span in plan.cut:
        index += 1
        writer.writerow([
            index, "cut", f"{span.start:.3f}", f"{span.end:.3f}",
            f"{span.duration:.3f}", "", "",
        ])
    return buffer.getvalue()


def write_csv(plan: CutPlan, path: PathLike) -> Path:
    target = Path(path)
    target.write_text(to_csv(plan), encoding="utf-8-sig")
    return target


def _timecode(seconds: float, fps: float) -> str:
    rate = int(round(fps)) or 30
    total_frames = int(round(max(0.0, seconds) * rate))
    frames = total_frames % rate
    total_seconds = total_frames // rate
    return "{:02d}:{:02d}:{:02d}:{:02d}".format(
        total_seconds // 3600,
        total_seconds // 60 % 60,
        total_seconds % 60,
        frames,
    )


def to_edl(plan: CutPlan, *, title: Optional[str] = None,
           record_start: float = 3600.0) -> str:
    """CMX3600 EDL。Premiere / Resolve / Final Cut など多くの編集ソフトが読める。

    録画開始位置は既定で 01:00:00:00 (編集ソフトの慣例)。
    """
    name = title or Path(plan.source).stem or "sequence"
    fps = plan.fps or 30.0
    rate = int(round(fps)) or 30
    lines = [f"TITLE: {name}", "FCM: NON-DROP FRAME", ""]

    channels = []
    if plan.has_video:
        channels.append("V")
    if plan.has_audio:
        channels.append("A")
    if not channels:
        channels = ["V"]

    number = 0
    cursor = 0.0
    for span in plan.keep:
        record_from = record_start + cursor
        record_to = record_from + span.duration
        for position, channel in enumerate(channels):
            number += 1
            lines.append(
                "{:03d}  AX       {:<5} C        {} {} {} {}".format(
                    number, channel,
                    _timecode(span.start, rate),
                    _timecode(span.end, rate),
                    _timecode(record_from, rate),
                    _timecode(record_to, rate),
                )
            )
            if position == 0:
                lines.append(f"* FROM CLIP NAME: {Path(plan.source).name}")
        cursor += span.duration
    lines.append("")
    return "\n".join(lines)


def write_edl(plan: CutPlan, path: PathLike, **kwargs) -> Path:
    target = Path(path)
    target.write_text(to_edl(plan, **kwargs), encoding="utf-8")
    return target


def write_plan(plan: CutPlan, path: PathLike, *, fmt: Optional[str] = None) -> Path:
    """拡張子から JSON / CSV / EDL を判定して書き出す。"""
    target = Path(path)
    suffix = (fmt or target.suffix.lstrip(".")).lower()
    if suffix in ("json", ""):
        return write_json(plan, target)
    if suffix == "csv":
        return write_csv(plan, target)
    if suffix in ("edl", "txt"):
        return write_edl(plan, target)
    raise ValueError(f"未対応の形式です: {suffix or '(拡張子なし)'} (json / csv / edl)")
