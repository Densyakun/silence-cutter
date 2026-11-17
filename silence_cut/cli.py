from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .detect import CutPlan
from .export import write_plan
from .media import MediaError, probe
from .pipeline import analyze_file
from .render import RenderSettings, play_file, render
from .settings import AnalysisSettings


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (ValueError, OSError):  # pragma: no cover
                pass


def _add_analysis_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("speech", "silence"), default="speech",
                        help="speech=無声カット (既定・発話区間のみ残す) / silence=無音カット")
    parser.add_argument("-t", "--threshold", type=float, default=AnalysisSettings.threshold_db,
                        help="無音とみなす音量 (dBFS、既定: -45)")
    parser.add_argument("-s", "--min-silence", type=float, default=AnalysisSettings.min_silence,
                        help="カットする最小の無音/非発話長 (秒、既定: 0.5)")
    parser.add_argument("-m", "--min-speech", type=float, default=AnalysisSettings.min_speech,
                        help="これより短い発話は無音に吸収 (秒、既定: 0.15)")
    parser.add_argument("--pad-start", type=float, default=AnalysisSettings.pad_start,
                        help="発話の前に残す余白 (秒、既定: 0.05)")
    parser.add_argument("--pad-end", type=float, default=AnalysisSettings.pad_end,
                        help="発話の後に残す余白 (秒、既定: 0.08)")
    parser.add_argument("--adaptive", action="store_true",
                        help="ノイズフロアからしきい値を自動推定する (無音カット用)")
    parser.add_argument("--adaptive-margin", type=float, default=AnalysisSettings.adaptive_margin_db,
                        help="自動しきい値のマージン (dB、既定: 9)")
    parser.add_argument("--frame-ms", type=float, default=AnalysisSettings.frame_ms,
                        help="解析フレーム長 (ms、既定: 20)")
    parser.add_argument("--snr-min", type=float, default=AnalysisSettings.snr_min_db,
                        help="発話の開始とみなすSNR (dB、無声カット用、既定: 8)")
    parser.add_argument("--snr-exit", type=float, default=AnalysisSettings.snr_exit_db,
                        help="発話の終了とみなすSNR (dB、無声カット用、既定: 4)")
    parser.add_argument("--hangover", type=float, default=AnalysisSettings.hangover,
                        help="発話終了後に発話扱いを続ける長さ (秒、既定: 0.15)")
    parser.add_argument("--noise-percentile", type=float,
                        default=AnalysisSettings.noise_percentile,
                        help="ノイズフロア推定のパーセンタイル (既定: 10)")
    parser.add_argument("--ffmpeg", help="ffmpeg のパス")
    parser.add_argument("--ffprobe", help="ffprobe のパス")


def _settings_from_args(args: argparse.Namespace) -> AnalysisSettings:
    return AnalysisSettings(
        detection=args.mode,
        threshold_db=args.threshold,
        min_silence=args.min_silence,
        min_speech=args.min_speech,
        pad_start=args.pad_start,
        pad_end=args.pad_end,
        adaptive=args.adaptive,
        adaptive_margin_db=args.adaptive_margin,
        frame_ms=args.frame_ms,
        snr_min_db=args.snr_min,
        snr_exit_db=args.snr_exit,
        hangover=args.hangover,
        noise_percentile=args.noise_percentile,
    ).validated()


class _ProgressPrinter:
    def __init__(self, label: str) -> None:
        self.label = label
        self.last = -1
        self.interactive = bool(getattr(sys.stderr, "isatty", lambda: False)())
        self.step = 1 if self.interactive else 10

    def __call__(self, value: float) -> None:
        percent = int(value * 100)
        if percent == self.last:
            return
        if percent < 100 and percent % self.step != 0 and percent - self.last < self.step:
            return
        self.last = percent
        if self.interactive:
            sys.stderr.write(f"\r{self.label} {percent:3d}%")
            if percent >= 100:
                sys.stderr.write("\n")
        else:
            sys.stderr.write(f"{self.label} {percent:3d}%\n")
        sys.stderr.flush()


def _print_plan(plan: CutPlan, limit: int = 40) -> None:
    mode_label = "無声カット (発話のみ)" if plan.settings.detection == "speech" else "無音カット"
    print(f"モード: {mode_label}")
    print(plan.describe())
    detail = f"  しきい値: {plan.threshold_db:.1f} dBFS"
    if plan.noise_db is not None:
        detail += f" / ノイズフロア: {plan.noise_db:.1f} dBFS"
    print(detail + f" / 元の長さ: {plan.duration:.2f} 秒")
    print()
    print("  残す区間:")
    for index, span in enumerate(plan.keep[:limit], start=1):
        print(f"    K{index:03d}  {span.start:8.2f} - {span.end:8.2f}  ({span.duration:6.2f} 秒)")
    if len(plan.keep) > limit:
        print(f"    ... 他 {len(plan.keep) - limit} 区間")
    print()
    print("  カットする区間:")
    for index, span in enumerate(plan.cut[:limit], start=1):
        print(f"    C{index:03d}  {span.start:8.2f} - {span.end:8.2f}  ({span.duration:6.2f} 秒)")
    if len(plan.cut) > limit:
        print(f"    ... 他 {len(plan.cut) - limit} 区間")


def cmd_probe(args: argparse.Namespace) -> int:
    info = probe(args.input, ffprobe=args.ffprobe)
    print(f"ファイル: {info.path}")
    print(f"  長さ: {info.duration:.2f} 秒 / fps: {info.fps or '-'}")
    print(f"  映像: {'あり' if info.has_video else 'なし'}"
          + (f" ({info.width}x{info.height})" if info.has_video else ""))
    print(f"  音声: {'あり' if info.has_audio else 'なし'}"
          + (f" ({info.audio_sample_rate} Hz)" if info.has_audio else ""))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    result = analyze_file(
        args.input, settings,
        ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        progress=_ProgressPrinter("解析中"),
    )
    plan = result.plan
    _print_plan(plan, limit=args.limit)

    if args.fps:
        plan.fps = args.fps
    written: List[Path] = []
    for option, fmt in (
        ("json", "json"), ("csv", "csv"), ("edl", "edl"), ("exo", "exo"),
        ("xml", "xml"), ("fcpxml", "fcpxml"),
    ):
        destination = getattr(args, option)
        if destination:
            written.append(write_plan(plan, destination, fmt=fmt))
    for path in written:
        print(f"書き出し: {path}")
    return 0


def cmd_cut(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    result = analyze_file(
        args.input, settings,
        ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        progress=_ProgressPrinter("解析中"),
    )
    plan = result.plan
    if args.fps:
        plan.fps = args.fps
    if args.plan:
        write_plan(plan, args.plan)
        print(f"カットプラン: {args.plan}")

    output = Path(args.output) if args.output else Path(args.input).with_name(
        f"{Path(args.input).stem}_cut{Path(args.input).suffix or '.mp4'}"
    )
    render_settings = RenderSettings(
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
    )
    print(plan.describe())
    try:
        render(plan, output, render_settings, ffmpeg=args.ffmpeg,
               progress=_ProgressPrinter("書き出し中"))
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    print(f"完了: {output}")
    if args.play:
        play_file(output)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main
    gui_main(initial_file=args.input)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="silence-cut",
        description="動画・音声から無音区間を検出し、カットプランを作成・書き出します。",
    )
    parser.add_argument("--version", action="version", version=f"silence-cut {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="素材の情報を表示する")
    probe_parser.add_argument("input")
    probe_parser.add_argument("--ffprobe")
    probe_parser.set_defaults(func=cmd_probe)

    analyze_parser = subparsers.add_parser(
        "analyze", help="解析してカットプランを表示・出力する",
    )
    analyze_parser.add_argument("input")
    _add_analysis_args(analyze_parser)
    analyze_parser.add_argument("--json", help="カットプラン JSON の保存先")
    analyze_parser.add_argument("--csv", help="区間一覧 CSV の保存先")
    analyze_parser.add_argument("--edl", help="CMX3600 EDL の保存先")
    analyze_parser.add_argument(
        "--exo", help="AviUtl 拡張編集オブジェクト (.exo) の保存先",
    )
    analyze_parser.add_argument(
        "--xml", help="Premiere Pro / DaVinci Resolve 用 XMEML XML の保存先",
    )
    analyze_parser.add_argument(
        "--fcpxml", help="Final Cut Pro / DaVinci Resolve 用 FCPXML の保存先",
    )
    analyze_parser.add_argument(
        "--fps", type=float, help="XML / EDL に使う fps (既定は素材から取得)",
    )
    analyze_parser.add_argument("--limit", type=int, default=40,
                                help="表示する区間の最大数 (既定: 40)")
    analyze_parser.set_defaults(func=cmd_analyze)

    cut_parser = subparsers.add_parser(
        "cut", help="解析して無音をカットしたファイルを書き出す",
    )
    cut_parser.add_argument("input")
    _add_analysis_args(cut_parser)
    cut_parser.add_argument("-o", "--output", help="出力ファイル (既定: <入力名>_cut.mp4)")
    cut_parser.add_argument("--plan", help="カットプランも保存する (拡張子で形式を判定)")
    cut_parser.add_argument("--fps", type=float, help="XML / EDL 用 fps (プラン保存時に使用)")
    cut_parser.add_argument("--crf", type=int, default=20, help="映像品質 (既定: 20)")
    cut_parser.add_argument("--preset", default="medium", help="x264 プリセット (既定: medium)")
    cut_parser.add_argument("--audio-bitrate", default="192k", help="音声ビットレート (既定: 192k)")
    cut_parser.add_argument("--play", action="store_true", help="完了後にプレーヤーで開く")
    cut_parser.set_defaults(func=cmd_cut)

    gui_parser = subparsers.add_parser("gui", help="GUI を起動する")
    gui_parser.add_argument("input", nargs="?", help="最初に開くファイル")
    gui_parser.set_defaults(func=cmd_gui)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MediaError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
