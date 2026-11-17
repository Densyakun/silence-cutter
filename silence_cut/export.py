from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
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


def _rate_rational(fps: float) -> tuple:
    """fps を (分子, 分母) の有理数にする。29.97 などの NTSC 系を保持する。"""
    if not fps or fps <= 0:
        return 30, 1
    ntsc = (
        (24000 / 1001, 24000, 1001),
        (30000 / 1001, 30000, 1001),
        (48000 / 1001, 48000, 1001),
        (60000 / 1001, 60000, 1001),
        (120000 / 1001, 120000, 1001),
    )
    for value, numerator, denominator in ntsc:
        if abs(fps - value) < 0.02:
            return numerator, denominator
    rounded = int(round(fps))
    return (rounded, 1) if rounded > 0 else (30, 1)


def _rate_timebase(fps: float) -> tuple:
    """XMEML / EDL 用の (nominal rate, NTSC フラグ)。"""
    if not fps or fps <= 0:
        return 30, False
    for nominal, value in ((24, 24000 / 1001), (30, 30000 / 1001),
                           (48, 48000 / 1001), (60, 60000 / 1001),
                           (120, 120000 / 1001)):
        if abs(fps - value) < 0.02:
            return nominal, True
    rounded = int(round(fps))
    return (rounded, False) if rounded > 0 else (30, False)


def _frames(seconds: float, rate: int) -> int:
    return int(round(max(0.0, seconds) * rate))


def _drop_frame_number(frame_number: int, rate: int) -> int:
    drop = 2 if rate == 30 else 4
    frames_per_minute = rate * 60 - drop
    frames_per_10_minutes = rate * 600 - drop * 9
    ten_minutes, remainder = divmod(frame_number, frames_per_10_minutes)
    if remainder < drop:
        return frame_number + drop * 9 * ten_minutes
    return (
        frame_number
        + drop * 9 * ten_minutes
        + drop * ((remainder - drop) // frames_per_minute)
    )


def _timecode(seconds: float, fps: float, drop_frame: bool = False) -> str:
    rate, _ = _rate_timebase(fps)
    if drop_frame:
        # ドロップフレームは実フレーム数 (= 実 fps) を数えてからラベルを補正する
        total_frames = int(round(max(0.0, seconds) * (fps or rate)))
        total_frames = _drop_frame_number(total_frames, rate)
    else:
        # 非ドロップは編集ソフトのタイムベース (24 / 25 / 30 ...) で数える
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

    29.97 / 59.94 fps ではドロップフレーム表記 (FCM: DROP FRAME) を使う。
    録画開始位置は既定で 01:00:00:00 (編集ソフトの慣例)。
    """
    name = title or Path(plan.source).stem or "sequence"
    fps = plan.fps or 30.0
    rate, ntsc = _rate_timebase(fps)
    drop_frame = ntsc and rate in (30, 60)
    lines = [
        f"TITLE: {name}",
        "FCM: DROP FRAME" if drop_frame else "FCM: NON-DROP FRAME",
        "",
    ]

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
                    _timecode(span.start, fps, drop_frame),
                    _timecode(span.end, fps, drop_frame),
                    _timecode(record_from, fps, drop_frame),
                    _timecode(record_to, fps, drop_frame),
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


def _aviutl_path(plan: CutPlan) -> str:
    if not plan.source:
        return ""
    return str(Path(plan.source).resolve())


def to_exo(plan: CutPlan) -> str:
    """AviUtl 拡張編集 (exedit) のオブジェクトファイル (.exo) を生成する。

    残す区間を動画レイヤー 1 / 音声レイヤー 2 に順番に並べる。AviUtl の
    フレームは 1 始まりなので、先頭オブジェクトは ``start=1`` から始まる。
    ファイルは Shift_JIS / CRLF で保存する必要がある (`write_exo` が行う)。
    """
    fps = plan.fps or 30.0
    numerator, denominator = _rate_rational(fps)
    source_total = _frames(plan.duration, fps)

    clips = []
    cursor = 1
    for span in plan.keep:
        start = max(0, min(source_total, _frames(span.start, fps)))
        end = max(start, min(source_total, _frames(span.end, fps)))
        length = end - start
        if length <= 0:
            continue
        clips.append((start, length, cursor))
        cursor += length
    total = max(1, cursor - 1)

    lines = [
        "[exedit]",
        f"width={plan.width or 1920}",
        f"height={plan.height or 1080}",
        f"rate={numerator}",
        f"scale={denominator}",
        f"length={total}",
        f"audio_rate={plan.audio_sample_rate or 48000}",
        "audio_ch=2",
        "",
    ]

    path = _aviutl_path(plan)
    index = 0
    for group, (source_start, length, timeline_start) in enumerate(clips, start=1):
        timeline_end = timeline_start + length - 1
        play_position = source_start + 1

        if plan.has_video:
            lines.extend([
                f"[{index}]",
                f"start={timeline_start}",
                f"end={timeline_end}",
                "layer=1",
                f"group={group}",
                "overlay=1",
                "camera=0",
                f"[{index}.0]",
                "_name=動画ファイル",
                f"再生位置={play_position}",
                "再生速度=100.0",
                "ループ再生=0",
                "アルファチャンネルを読み込む=0",
                f"file={path}",
                f"[{index}.1]",
                "_name=標準描画",
                "X=0.0",
                "Y=0.0",
                "Z=0.0",
                "拡大率=100.00",
                "透明度=0.0",
                "回転=0.00",
                "blend=0",
                "",
            ])
            index += 1

        if plan.has_audio:
            linked = plan.has_video
            lines.extend([
                f"[{index}]",
                f"start={timeline_start}",
                f"end={timeline_end}",
                f"layer={2 if linked else 1}",
                f"group={group}",
                "overlay=1",
                "audio=1",
                f"[{index}.0]",
                "_name=音声ファイル",
                "再生位置=0.00" if linked else f"再生位置={play_position}",
                "再生速度=100.0",
                "ループ再生=0",
                f"動画ファイルと連携={1 if linked else 0}",
                f"file={'' if linked else path}",
                f"[{index}.1]",
                "_name=標準再生",
                "音量=100.0",
                "左右=0.0",
                "",
            ])
            index += 1

    return "\n".join(lines)


def write_exo(plan: CutPlan, path: PathLike) -> Path:
    target = Path(path)
    try:
        data = to_exo(plan).replace("\n", "\r\n").encode("cp932")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "AviUtl 用 .exo は Shift_JIS で保存するため、ファイルパスに "
            "Shift_JIS に無い文字が含まれています。素材を別のパスへ移動してください。"
        ) from exc
    target.write_bytes(data)
    return target


def _source_uri(plan: CutPlan, source_url: Optional[str] = None) -> str:
    if source_url:
        return source_url
    if not plan.source:
        return "file://localhost/"
    try:
        return Path(plan.source).resolve().as_uri()
    except ValueError:  # pragma: no cover - Windows 以外の異常パス
        return "file://localhost/"


def _clip_name(plan: CutPlan, title: Optional[str]) -> str:
    return title or Path(plan.source).name or "clip"


def _xml_bytes(root: ET.Element, doctype: str) -> bytes:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!DOCTYPE {doctype}>\n{body}\n"
    ).encode("utf-8")


def _fcpxml_time(frames: int, numerator: int, denominator: int) -> str:
    if frames <= 0:
        return "0s"
    return f"{frames * denominator}/{numerator}s"


def _fcpxml_format_name(plan: CutPlan, fps: float) -> str:
    width = plan.width or 1920
    height = plan.height or 1080
    if abs(fps - round(fps)) < 0.001:
        label = str(int(round(fps)))
    else:
        label = str(int(round(fps * 100)))
    return f"FFVideoFormat{height}p{label}"


def _audio_rate_label(sample_rate: int) -> str:
    if not sample_rate:
        return "48k"
    return f"{sample_rate / 1000:g}k"


def to_fcpxml(plan: CutPlan, *, title: Optional[str] = None,
              source_url: Optional[str] = None) -> str:
    """Final Cut Pro / DaVinci Resolve が読み込める FCPXML を生成する。

    素材を参照する ``asset-clip`` を残す区間ぶんだけ並べるため、
    読み込むだけでカット済みのタイムラインになる (再リンク不要)。
    """
    fps = plan.fps or 30.0
    numerator, denominator = _rate_rational(fps)
    frame_duration = f"{denominator}/{numerator}s"
    source_frames = _frames(plan.duration, numerator / denominator)

    name = _clip_name(plan, title)
    sequence_name = f"{Path(name).stem}_cut" or "sequence"

    root = ET.Element("fcpxml", {"version": "1.9"})
    resources = ET.SubElement(root, "resources")
    format_attrs = {
        "id": "r1",
        "name": _fcpxml_format_name(plan, fps),
        "frameDuration": frame_duration,
        "width": str(plan.width or 1920),
        "height": str(plan.height or 1080),
    }
    ET.SubElement(resources, "format", format_attrs)

    has_video = "1" if plan.has_video else "0"
    asset_attrs = {
        "id": "r2",
        "name": name,
        "start": "0s",
        "duration": _fcpxml_time(source_frames, numerator, denominator),
        "hasVideo": has_video,
    }
    if plan.has_video:
        asset_attrs["format"] = "r1"
    if plan.has_audio:
        asset_attrs.update({
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": str(plan.audio_sample_rate or 48000),
        })
    asset = ET.SubElement(resources, "asset", asset_attrs)
    ET.SubElement(asset, "media-rep", {
        "kind": "original-media",
        "src": _source_uri(plan, source_url),
    })

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "Silence Cut"})
    project = ET.SubElement(event, "project", {"name": sequence_name})

    total_frames = 0
    clips = []
    for span in plan.keep:
        start = min(source_frames, max(0, _frames(span.start, numerator / denominator)))
        end = min(source_frames, max(start, _frames(span.end, numerator / denominator)))
        length = end - start
        if length <= 0:
            continue
        clips.append((total_frames, start, length))
        total_frames += length

    sequence_attrs = {
        "format": "r1",
        "duration": _fcpxml_time(total_frames, numerator, denominator),
        "tcStart": "0s",
        "tcFormat": "DF" if denominator != 1 and (numerator / denominator) in (
            30000 / 1001, 60000 / 1001, 120000 / 1001,
        ) else "NDF",
        "audioLayout": "stereo",
        "audioRate": _audio_rate_label(plan.audio_sample_rate),
    }
    sequence = ET.SubElement(project, "sequence", sequence_attrs)
    spine = ET.SubElement(sequence, "spine")
    for offset, start, length in clips:
        clip_attrs = {
            "ref": "r2",
            "name": name,
            "offset": _fcpxml_time(offset, numerator, denominator),
            "start": _fcpxml_time(start, numerator, denominator),
            "duration": _fcpxml_time(length, numerator, denominator),
        }
        if plan.has_audio:
            clip_attrs["audioRole"] = "dialogue"
        ET.SubElement(spine, "asset-clip", clip_attrs)

    return _xml_bytes(root, "fcpxml").decode("utf-8")


def write_fcpxml(plan: CutPlan, path: PathLike, **kwargs) -> Path:
    target = Path(path)
    target.write_bytes(to_fcpxml(plan, **kwargs).encode("utf-8"))
    return target


def _append_xmeml_rate(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _append_xmeml_file(parent: ET.Element, file_id: str, plan: CutPlan,
                       timebase: int, ntsc: bool, duration: int,
                       source_url: Optional[str]) -> None:
    file_node = ET.SubElement(parent, "file", {"id": file_id})
    ET.SubElement(file_node, "name").text = _clip_name(plan, None)
    ET.SubElement(file_node, "pathurl").text = _source_uri(plan, source_url)
    _append_xmeml_rate(file_node, timebase, ntsc)
    ET.SubElement(file_node, "duration").text = str(duration)
    media = ET.SubElement(file_node, "media")
    if plan.has_video:
        video = ET.SubElement(media, "video")
        characteristics = ET.SubElement(video, "samplecharacteristics")
        ET.SubElement(characteristics, "width").text = str(plan.width or 1920)
        ET.SubElement(characteristics, "height").text = str(plan.height or 1080)
        ET.SubElement(characteristics, "anamorphic").text = "FALSE"
        ET.SubElement(characteristics, "pixelaspectratio").text = "square"
        ET.SubElement(characteristics, "fielddominance").text = "none"
    if plan.has_audio:
        audio = ET.SubElement(media, "audio")
        characteristics = ET.SubElement(audio, "samplecharacteristics")
        ET.SubElement(characteristics, "depth").text = "16"
        ET.SubElement(characteristics, "samplerate").text = str(
            plan.audio_sample_rate or 48000,
        )


def to_xmeml(plan: CutPlan, *, title: Optional[str] = None,
             source_url: Optional[str] = None) -> str:
    """Premiere Pro / DaVinci Resolve が読み込める XMEML (FCP7 XML)。

    残す区間だけを並べたシーケンスを出力する。素材は ``pathurl`` で
    参照するため、プロジェクトへ読み込むとカット済みタイムラインになる。
    """
    fps = plan.fps or 30.0
    timebase, ntsc = _rate_timebase(fps)
    source_frames = _frames(plan.duration, timebase)
    name = _clip_name(plan, title)

    root = ET.Element("xmeml", {"version": "5"})
    sequence = ET.SubElement(root, "sequence")
    ET.SubElement(sequence, "name").text = f"{Path(name).stem}_cut" or "sequence"
    ET.SubElement(sequence, "duration").text = "0"
    _append_xmeml_rate(sequence, timebase, ntsc)
    media = ET.SubElement(sequence, "media")

    total_frames = 0
    clips = []
    for span in plan.keep:
        start = min(source_frames, max(0, _frames(span.start, timebase)))
        end = min(source_frames, max(start, _frames(span.end, timebase)))
        length = end - start
        if length <= 0:
            continue
        clips.append((total_frames, start, end, length))
        total_frames += length

    for tag, channel in (("video", "video"), ("audio", "audio")):
        if channel == "video" and not plan.has_video:
            continue
        if channel == "audio" and not plan.has_audio:
            continue
        node = ET.SubElement(media, tag)
        if channel == "video":
            fmt = ET.SubElement(node, "format")
            characteristics = ET.SubElement(fmt, "samplecharacteristics")
            _append_xmeml_rate(characteristics, timebase, ntsc)
            ET.SubElement(characteristics, "width").text = str(plan.width or 1920)
            ET.SubElement(characteristics, "height").text = str(plan.height or 1080)
            ET.SubElement(characteristics, "anamorphic").text = "FALSE"
            ET.SubElement(characteristics, "pixelaspectratio").text = "square"
            ET.SubElement(characteristics, "fielddominance").text = "none"
        track = ET.SubElement(node, "track")
        for index, (record, start, end, length) in enumerate(clips, start=1):
            clipitem = ET.SubElement(
                track, "clipitem", {"id": f"{channel}-clipitem-{index}"},
            )
            ET.SubElement(clipitem, "name").text = name
            ET.SubElement(clipitem, "duration").text = str(source_frames)
            _append_xmeml_rate(clipitem, timebase, ntsc)
            ET.SubElement(clipitem, "start").text = str(record)
            ET.SubElement(clipitem, "end").text = str(record + length)
            ET.SubElement(clipitem, "in").text = str(start)
            ET.SubElement(clipitem, "out").text = str(end)
            _append_xmeml_file(
                clipitem, f"file-{channel}-{index}", plan, timebase, ntsc,
                source_frames, source_url,
            )
            if channel == "audio":
                source_track = ET.SubElement(clipitem, "sourcetrack")
                ET.SubElement(source_track, "mediatype").text = "audio"
                ET.SubElement(source_track, "trackindex").text = "1"

    sequence.find("duration").text = str(total_frames)
    return _xml_bytes(root, "xmeml").decode("utf-8")


def write_xmeml(plan: CutPlan, path: PathLike, **kwargs) -> Path:
    target = Path(path)
    target.write_bytes(to_xmeml(plan, **kwargs).encode("utf-8"))
    return target


def write_plan(plan: CutPlan, path: PathLike, *, fmt: Optional[str] = None) -> Path:
    """拡張子から JSON / CSV / EDL / XML / FCPXML を判定して書き出す。"""
    target = Path(path)
    suffix = (fmt or target.suffix.lstrip(".")).lower()
    if suffix in ("json", ""):
        return write_json(plan, target)
    if suffix == "csv":
        return write_csv(plan, target)
    if suffix in ("edl", "txt"):
        return write_edl(plan, target)
    if suffix == "exo":
        return write_exo(plan, target)
    if suffix in ("xml", "xmeml"):
        return write_xmeml(plan, target)
    if suffix == "fcpxml":
        return write_fcpxml(plan, target)
    raise ValueError(
        f"未対応の形式です: {suffix or '(拡張子なし)'} "
        "(json / csv / edl / exo / xml / fcpxml)"
    )
