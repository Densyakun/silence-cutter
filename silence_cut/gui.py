from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from .detect import CutPlan
from .export import write_plan
from .media import MediaError
from .pipeline import AnalysisResult, analyze_file
from .preview import PreviewSession
from .render import RenderSettings, render
from .settings import AnalysisSettings

APP_TITLE = "Silence Cut - 無音カット"
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts")
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff")


def _fmt_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


class SilenceCutApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.result: Optional[AnalysisResult] = None
        self._busy = False
        self._queue: "queue.Queue" = queue.Queue()
        self._on_success: Optional[Callable] = None
        self._label = ""
        self._waveform_job: Optional[str] = None
        self._controls = []
        self.preview_window: Optional[tk.Toplevel] = None
        self.preview_session: Optional[PreviewSession] = None
        self._preview_video: Optional[tk.Frame] = None
        self._preview_after: Optional[str] = None
        self._preview_dragging = False
        self._file_drop = None

        root.title(APP_TITLE)
        root.geometry("1100x820")
        root.minsize(900, 680)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._speech_controls = []
        self._silence_controls = []
        self.var_file = tk.StringVar()
        self.var_mode = tk.StringVar(value="speech")
        self.var_threshold = tk.StringVar(value=f"{AnalysisSettings.threshold_db:g}")
        self.var_min_silence = tk.StringVar(value=f"{AnalysisSettings.min_silence:g}")
        self.var_min_speech = tk.StringVar(value=f"{AnalysisSettings.min_speech:g}")
        self.var_pad_start = tk.StringVar(value=f"{AnalysisSettings.pad_start:g}")
        self.var_pad_end = tk.StringVar(value=f"{AnalysisSettings.pad_end:g}")
        self.var_frame_ms = tk.StringVar(value=f"{AnalysisSettings.frame_ms:g}")
        self.var_snr = tk.StringVar(value=f"{AnalysisSettings.snr_min_db:g}")
        self.var_snr_exit = tk.StringVar(value=f"{AnalysisSettings.snr_exit_db:g}")
        self.var_hangover = tk.StringVar(value=f"{AnalysisSettings.hangover:g}")
        self.var_noise_percentile = tk.StringVar(
            value=f"{AnalysisSettings.noise_percentile:g}",
        )
        self.var_adaptive = tk.BooleanVar(value=False)
        self.var_adaptive_margin = tk.StringVar(
            value=f"{AnalysisSettings.adaptive_margin_db:g}",
        )
        self.var_mode_hint = tk.StringVar(value="")
        self.var_status = tk.StringVar(
            value="動画または音声ファイルを選択してください（ここへドラッグ＆ドロップも可）",
        )
        self.var_summary = tk.StringVar(value="")

        self._build()
        self._apply_mode_state()
        self._set_export_enabled(False)
        self._enable_file_drop()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        file_row = ttk.Frame(outer)
        file_row.pack(fill="x")
        ttk.Label(file_row, text="入力ファイル").pack(side="left")
        entry = ttk.Entry(file_row, textvariable=self.var_file)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        browse = ttk.Button(file_row, text="参照...", command=self.browse)
        browse.pack(side="left")
        self._controls.append(browse)
        self.file_drop_hint = ttk.Label(
            file_row, text="ドラッグ＆ドロップ可", foreground="#6b7280",
        )
        self.file_drop_hint.pack(side="left", padx=(8, 0))

        params = ttk.LabelFrame(outer, text="解析パラメータ", padding=8)
        params.pack(fill="x", pady=(10, 0))

        mode_row = ttk.Frame(params)
        mode_row.pack(fill="x")
        ttk.Label(mode_row, text="検出モード:").pack(side="left")
        radio_speech = ttk.Radiobutton(
            mode_row, text="無声カット（推奨・発話区間のみ残す）",
            value="speech", variable=self.var_mode, command=self._on_mode_changed,
        )
        radio_speech.pack(side="left", padx=(8, 0))
        radio_silence = ttk.Radiobutton(
            mode_row, text="無音カット（静かな区間を除去）",
            value="silence", variable=self.var_mode, command=self._on_mode_changed,
        )
        radio_silence.pack(side="left", padx=(12, 0))
        self._controls.extend([radio_speech, radio_silence])

        ttk.Label(
            params, textvariable=self.var_mode_hint, foreground="#4b5563",
        ).pack(anchor="w", pady=(4, 0))

        specific = ttk.Frame(params)
        specific.pack(fill="x", pady=(8, 0))
        specific.columnconfigure(0, weight=1)
        specific.columnconfigure(1, weight=1)

        speech_frame = ttk.LabelFrame(
            specific, text="無声カット専用（発話区間検出）", padding=6,
        )
        speech_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._add_param_field(
            speech_frame, "開始SNR (dB)", self.var_snr,
            from_=0, to=60, increment=1, controls=self._speech_controls,
        )
        self._add_param_field(
            speech_frame, "終了SNR (dB)", self.var_snr_exit,
            from_=0, to=60, increment=1, controls=self._speech_controls,
        )
        self._add_param_field(
            speech_frame, "発話ホールド (秒)", self.var_hangover,
            from_=0, to=5, increment=0.05, controls=self._speech_controls,
        )
        self._add_param_field(
            speech_frame, "ノイズフロア推定 (%)", self.var_noise_percentile,
            from_=0, to=50, increment=1, controls=self._speech_controls,
        )

        silence_frame = ttk.LabelFrame(
            specific, text="無音カット専用（音量しきい値）", padding=6,
        )
        silence_frame.grid(row=0, column=1, sticky="nsew")
        silence_row = ttk.Frame(silence_frame)
        silence_row.pack(fill="x")
        self._add_param_field(
            silence_row, "しきい値 (dBFS)", self.var_threshold,
            from_=-100, to=0, increment=1, controls=self._silence_controls,
        )
        self._add_param_field(
            silence_row, "自動マージン (dB)", self.var_adaptive_margin,
            from_=0, to=60, increment=1, controls=self._silence_controls,
        )
        adaptive = ttk.Checkbutton(
            silence_frame, text="しきい値を自動推定する",
            variable=self.var_adaptive,
        )
        adaptive.pack(anchor="w", pady=(4, 0))
        self._controls.append(adaptive)
        self._silence_controls.append(adaptive)

        common = ttk.LabelFrame(
            params, text="共通設定（両モードで使用）", padding=6,
        )
        common.pack(fill="x", pady=(8, 0))
        self._add_param_field(
            common, "最小無音/非発話 (秒)", self.var_min_silence,
            from_=0, to=600, increment=0.1,
        )
        self._add_param_field(
            common, "最小発話 (秒)", self.var_min_speech,
            from_=0, to=60, increment=0.05,
        )
        self._add_param_field(
            common, "前余白 (秒)", self.var_pad_start,
            from_=0, to=10, increment=0.01,
        )
        self._add_param_field(
            common, "後余白 (秒)", self.var_pad_end,
            from_=0, to=10, increment=0.01,
        )
        self._add_param_field(
            common, "解析フレーム (ms)", self.var_frame_ms,
            from_=1, to=200, increment=1,
        )

        button_row = ttk.Frame(outer)
        button_row.pack(fill="x", pady=10)
        self.btn_analyze = ttk.Button(button_row, text="解析", command=self.analyze)
        self.btn_analyze.pack(side="left")
        self.btn_preview = ttk.Button(button_row, text="プレビュー", command=self.preview)
        self.btn_preview.pack(side="left", padx=6)
        self.btn_render = ttk.Button(button_row, text="書き出し...", command=self.export_video)
        self.btn_render.pack(side="left")
        self.btn_plan = ttk.Button(button_row, text="カットリスト保存...", command=self.export_plan)
        self.btn_plan.pack(side="left", padx=6)
        self._controls.extend([self.btn_analyze, self.btn_preview, self.btn_render, self.btn_plan])

        ttk.Label(button_row, textvariable=self.var_summary).pack(side="right")

        canvas_frame = ttk.LabelFrame(outer, text="波形 (赤: カット / 緑: 残す)", padding=4)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            canvas_frame, background="#15171c", highlightthickness=0, height=260,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._schedule_redraw)

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=False, pady=(8, 0))
        columns = ("index", "kind", "start", "end", "length")
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=7,
        )
        for key, text, width in (
            ("index", "#", 50), ("kind", "種別", 80), ("start", "開始 (秒)", 110),
            ("end", "終了 (秒)", 110), ("length", "長さ (秒)", 110),
        ):
            self.table.heading(key, text=text)
            self.table.column(key, width=width, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.tag_configure("cut", foreground="#b3261e")
        self.table.tag_configure("keep", foreground="#1a7f37")

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(bottom, textvariable=self.var_status, width=48, anchor="e").pack(
            side="left", padx=(8, 0),
        )

    def _add_param_field(
        self,
        parent,
        label: str,
        variable: tk.StringVar,
        *,
        from_: float,
        to: float,
        increment: float,
        width: int = 9,
        controls: Optional[List] = None,
    ) -> ttk.Spinbox:
        block = ttk.Frame(parent)
        block.pack(side="left", padx=(0, 12))
        ttk.Label(block, text=label).pack(anchor="w")
        spin = ttk.Spinbox(
            block, textvariable=variable, from_=from_, to=to,
            increment=increment, width=width,
        )
        spin.pack(anchor="w")
        self._controls.append(spin)
        if controls is not None:
            controls.append(spin)
        return spin

    def _on_mode_changed(self) -> None:
        self._apply_mode_state()

    def _apply_mode_state(self) -> None:
        mode = self.var_mode.get()
        if self._busy:
            speech_state = silence_state = "disabled"
        else:
            speech_state = "normal" if mode == "speech" else "disabled"
            silence_state = "normal" if mode == "silence" else "disabled"
        for widget in self._speech_controls:
            try:
                widget.configure(state=speech_state)
            except tk.TclError:
                pass
        for widget in self._silence_controls:
            try:
                widget.configure(state=silence_state)
            except tk.TclError:
                pass
        if mode == "speech":
            self.var_mode_hint.set(
                "無声カット: SNR（開始・終了）と発話ホールドを使います。"
                "しきい値・自動推定は使いません。",
            )
        else:
            self.var_mode_hint.set(
                "無音カット: 音量しきい値（または自動推定）を使います。"
                "SNR系の設定は使いません。",
            )

    def _set_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self._controls:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        if enabled:
            self._apply_mode_state()

    def _set_export_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.btn_preview, self.btn_render, self.btn_plan):
            widget.configure(state=state)

    # ------------------------------------------------------------- actions
    def browse(self) -> None:
        path = filedialog.askopenfilename(
            title="動画または音声ファイルを選択",
            filetypes=[
                ("動画・音声", " ".join(f"*{suffix}" for suffix in VIDEO_SUFFIXES + AUDIO_SUFFIXES)),
                ("すべてのファイル", "*.*"),
            ],
        )
        if path:
            self.var_file.set(path)
            self.load_file(path)

    def _enable_file_drop(self) -> None:
        try:
            self.root.update_idletasks()
            from .dnd import enable_file_drop
            self._file_drop = enable_file_drop(self.root, self._on_files_dropped)
        except Exception:
            self._file_drop = None
        if self._file_drop is None:
            self.file_drop_hint.pack_forget()
            self.var_status.set("動画または音声ファイルを選択してください")

    def _on_files_dropped(self, paths: List[str]) -> None:
        try:
            self.root.after_idle(lambda: self._load_dropped_paths(paths))
        except tk.TclError:
            pass

    def _load_dropped_paths(self, paths: List[str]) -> None:
        if self._busy:
            self.var_status.set("処理中はファイルを変更できません")
            return
        files = [path for path in paths if Path(path).is_file()]
        if not files:
            return
        supported = VIDEO_SUFFIXES + AUDIO_SUFFIXES
        chosen = next(
            (path for path in files if Path(path).suffix.lower() in supported),
            files[0],
        )
        self.load_file(chosen)
        self.var_status.set(f"読み込みました: {Path(chosen).name}")

    def load_file(self, path: str) -> None:
        self._close_preview()
        self.var_file.set(path)
        self.result = None
        self._set_export_enabled(False)
        self.table.delete(*self.table.get_children())
        self.canvas.delete("all")
        self.var_summary.set("")
        self.var_status.set("「解析」を押してください")

    def _read_settings(self) -> AnalysisSettings:
        try:
            return AnalysisSettings(
                detection=self.var_mode.get(),
                threshold_db=float(self.var_threshold.get()),
                min_silence=float(self.var_min_silence.get()),
                min_speech=float(self.var_min_speech.get()),
                pad_start=float(self.var_pad_start.get()),
                pad_end=float(self.var_pad_end.get()),
                adaptive=bool(self.var_adaptive.get()),
                adaptive_margin_db=float(self.var_adaptive_margin.get()),
                frame_ms=float(self.var_frame_ms.get()),
                snr_min_db=float(self.var_snr.get()),
                snr_exit_db=float(self.var_snr_exit.get()),
                hangover=float(self.var_hangover.get()),
                noise_percentile=float(self.var_noise_percentile.get()),
            ).validated()
        except ValueError as exc:
            raise ValueError(f"解析パラメータを確認してください: {exc}") from exc

    def _current_input(self) -> Optional[Path]:
        path = Path(self.var_file.get().strip())
        if not path.is_file():
            messagebox.showwarning(APP_TITLE, "入力ファイルを選択してください。")
            return None
        return path

    def analyze(self) -> None:
        path = self._current_input()
        if path is None:
            return
        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self._close_preview()
        self._run(
            "解析中",
            lambda report: analyze_file(path, settings, progress=report),
            self._on_analyzed,
        )

    def _on_analyzed(self, result: object) -> None:
        assert isinstance(result, AnalysisResult)
        self.result = result
        self._set_export_enabled(True)
        self.var_summary.set(result.plan.describe())
        mode_label = "無声カット" if result.plan.settings.detection == "speech" else "無音カット"
        detail = f"{mode_label} / しきい値 {result.plan.threshold_db:.1f} dBFS"
        if result.plan.noise_db is not None:
            detail += f" / ノイズフロア {result.plan.noise_db:.1f} dBFS"
        self.var_status.set(f"{detail} / 元の長さ {_fmt_time(result.plan.duration)}")
        self._fill_table(result.plan)
        self._draw_waveform()
        self.progress["value"] = 0

    def _fill_table(self, plan: CutPlan) -> None:
        self.table.delete(*self.table.get_children())
        index = 0
        for span in plan.keep:
            index += 1
            self.table.insert("", "end", tags=("keep",), values=(
                index, "残す", f"{span.start:.2f}", f"{span.end:.2f}", f"{span.duration:.2f}",
            ))
        for span in plan.cut:
            index += 1
            self.table.insert("", "end", tags=("cut",), values=(
                index, "カット", f"{span.start:.2f}", f"{span.end:.2f}", f"{span.duration:.2f}",
            ))

    def preview(self) -> None:
        plan = self._ready_plan()
        if plan is None:
            return
        if not plan.keep:
            messagebox.showwarning(
                APP_TITLE,
                "残す区間がありません。しきい値や最小無音長を調整してください。",
            )
            return
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self._toggle_preview()
            return
        self._open_preview(plan)

    def _open_preview(self, plan: CutPlan) -> None:
        window = tk.Toplevel(self.root)
        window.title("Silence Cut - プレビュー")
        window.geometry("900x600")
        window.minsize(640, 420)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_preview)

        outer = ttk.Frame(window, padding=8)
        outer.pack(fill="both", expand=True)

        self._preview_video = tk.Frame(outer, background="#000000", height=360)
        self._preview_video.pack(fill="both", expand=True)
        self._preview_video.bind("<Configure>", self._on_preview_video_configure)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(8, 0))
        self.btn_preview_play = ttk.Button(
            controls, text="一時停止", width=10, command=self._toggle_preview,
        )
        self.btn_preview_play.pack(side="left")
        ttk.Button(
            controls, text="停止", command=self._stop_preview,
        ).pack(side="left", padx=(6, 0))
        self.var_preview_time = tk.StringVar()
        ttk.Label(controls, textvariable=self.var_preview_time).pack(side="right")

        self._preview_scale = ttk.Scale(
            outer, from_=0.0, to=max(0.001, plan.output_duration),
            orient="horizontal", command=self._on_preview_scale,
        )
        self._preview_scale.pack(fill="x", pady=(8, 4))
        self._preview_scale.bind("<ButtonPress-1>", self._on_preview_seek_start)
        self._preview_scale.bind("<ButtonRelease-1>", self._on_preview_seek_end)

        self.var_preview_status = tk.StringVar(value="準備中...")
        ttk.Label(outer, textvariable=self.var_preview_status).pack(anchor="w")

        window.update_idletasks()
        width = max(1, self._preview_video.winfo_width())
        height = max(1, self._preview_video.winfo_height())
        try:
            session = PreviewSession(
                plan, video_parent_hwnd=self._preview_video.winfo_id(),
            )
        except (MediaError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            window.destroy()
            return

        self.preview_window = window
        self.preview_session = session
        self._preview_dragging = False
        self.var_preview_time.set(
            f"{_fmt_time(0.0)} / {_fmt_time(session.duration)}",
        )
        session.resize_player(width, height)
        try:
            session.play(0.0)
        except (MediaError, ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            self._close_preview()
            return
        self._update_preview()

    def _on_preview_video_configure(self, event: tk.Event) -> None:
        if self.preview_session is not None:
            self.preview_session.resize_player(event.width, event.height)

    def _toggle_preview(self) -> None:
        session = self.preview_session
        if session is None:
            return
        try:
            if session.is_playing:
                session.pause()
            else:
                session.play()
        except (MediaError, ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def _stop_preview(self) -> None:
        session = self.preview_session
        if session is not None:
            session.stop()

    def _on_preview_scale(self, value: str) -> None:
        if not self._preview_dragging or self.preview_session is None:
            return
        try:
            position = float(value)
        except ValueError:
            return
        self.var_preview_time.set(
            f"{_fmt_time(position)} / {_fmt_time(self.preview_session.duration)}",
        )

    def _on_preview_seek_start(self, _event: Optional[tk.Event] = None) -> None:
        self._preview_dragging = True

    def _on_preview_seek_end(self, _event: Optional[tk.Event] = None) -> None:
        self._preview_dragging = False
        session = self.preview_session
        if session is None:
            return
        session.seek(float(self._preview_scale.get()))

    def _update_preview(self) -> None:
        window = self.preview_window
        if window is None or not window.winfo_exists():
            return
        session = self.preview_session
        if session is not None:
            session.poll()
            position = session.position
            if not self._preview_dragging:
                self._preview_scale.set(position)
                self.var_preview_time.set(
                    f"{_fmt_time(position)} / {_fmt_time(session.duration)}",
                )
            if session.error:
                self.var_preview_status.set(f"エラー: {session.error}")
            elif session.is_playing:
                self.var_preview_status.set("再生中")
            elif session.has_pending_seek:
                self.var_preview_status.set("一時停止中（再生するとこの位置から再開します）")
            elif position >= session.duration - 0.5:
                self.var_preview_status.set("最後まで再生しました")
            else:
                self.var_preview_status.set("一時停止")
            self.btn_preview_play.configure(
                text="一時停止" if session.is_playing else "再生",
            )
        self._preview_after = self.root.after(100, self._update_preview)

    def _close_preview(self) -> None:
        if self._preview_after is not None:
            try:
                self.root.after_cancel(self._preview_after)
            except tk.TclError:
                pass
            self._preview_after = None
        if self.preview_session is not None:
            self.preview_session.close()
            self.preview_session = None
        self._preview_video = None
        if self.preview_window is not None:
            try:
                self.preview_window.destroy()
            except tk.TclError:
                pass
            self.preview_window = None

    def _on_close(self) -> None:
        if self._file_drop is not None:
            try:
                self._file_drop.detach()
            except Exception:
                pass
            self._file_drop = None
        self._close_preview()
        self.root.destroy()

    def export_video(self) -> None:
        plan = self._ready_plan()
        if plan is None:
            return
        source = Path(plan.source)
        if plan.has_video:
            default_suffix = ".mp4"
            filetypes = [("MP4", "*.mp4"), ("MOV", "*.mov"), ("MKV", "*.mkv"),
                         ("すべてのファイル", "*.*")]
        else:
            default_suffix = ".wav"
            filetypes = [("WAV", "*.wav"), ("MP3", "*.mp3"), ("M4A", "*.m4a"),
                         ("FLAC", "*.flac"), ("すべてのファイル", "*.*")]
        target = filedialog.asksaveasfilename(
            title="書き出し先を選択",
            initialdir=str(source.parent),
            initialfile=f"{source.stem}_cut{default_suffix}",
            defaultextension=default_suffix,
            filetypes=filetypes,
        )
        if not target:
            return
        self._run(
            "書き出し中",
            lambda report: render(plan, target, RenderSettings(), progress=report),
            lambda path: self.var_status.set(f"書き出しました: {path}"),
        )

    def export_plan(self) -> None:
        plan = self._ready_plan()
        if plan is None:
            return
        source = Path(plan.source)
        target = filedialog.asksaveasfilename(
            title="カットリストの保存先を選択",
            initialdir=str(source.parent),
            initialfile=f"{source.stem}_cutplan.json",
            defaultextension=".json",
            filetypes=[
                ("カットプラン JSON", "*.json"),
                ("区間一覧 CSV", "*.csv"),
                ("CMX3600 EDL", "*.edl"),
                ("AviUtl 拡張編集オブジェクト", "*.exo"),
                ("Premiere Pro / DaVinci Resolve XML", "*.xml"),
                ("Final Cut Pro XML", "*.fcpxml"),
            ],
        )
        if not target:
            return
        try:
            written = write_plan(plan, target)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.var_status.set(f"保存しました: {written}")

    def _ready_plan(self) -> Optional[CutPlan]:
        if self.result is None:
            messagebox.showwarning(APP_TITLE, "先に解析してください。")
            return None
        return self.result.plan

    # ------------------------------------------------------------- worker
    def _run(self, label: str, task: Callable, on_success: Callable) -> None:
        if self._busy:
            return
        self._busy = True
        self._label = label
        self._on_success = on_success
        self._queue = queue.Queue()
        self.progress["value"] = 0
        self.var_status.set(f"{label}... 0%")
        self._set_controls(False)
        self._set_export_enabled(False)

        def worker() -> None:
            try:
                value = task(self._report)
            except Exception as exc:  # noqa: BLE001 - GUI 側でまとめて表示
                self._queue.put(("error", exc))
            else:
                self._queue.put(("done", value))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(80, self._poll)

    def _report(self, value: float) -> None:
        self._queue.put(("progress", float(value)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    percent = max(0.0, min(100.0, payload * 100.0))
                    self.progress["value"] = percent
                    self.var_status.set(f"{self._label}... {int(percent)}%")
                elif kind == "done":
                    self._finish()
                    if self._on_success:
                        self._on_success(payload)
                    return
                elif kind == "error":
                    self._finish()
                    self._show_error(payload)
                    return
        except queue.Empty:
            pass
        if self._busy:
            self.root.after(80, self._poll)

    def _finish(self) -> None:
        self._busy = False
        self._set_controls(True)
        if self.result is not None:
            self._set_export_enabled(True)

    def _show_error(self, exc: BaseException) -> None:
        if isinstance(exc, (MediaError, ValueError)):
            message = str(exc)
        else:
            message = f"{type(exc).__name__}: {exc}"
        self.var_status.set("エラーが発生しました")
        messagebox.showerror(APP_TITLE, message)

    # ----------------------------------------------------------- waveform
    def _schedule_redraw(self, _event: Optional[tk.Event] = None) -> None:
        if self._waveform_job:
            self.root.after_cancel(self._waveform_job)
        self._waveform_job = self.root.after(120, self._draw_waveform)

    def _draw_waveform(self) -> None:
        self._waveform_job = None
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        if self.result is None:
            canvas.create_text(
                width / 2, height / 2, text="解析するとここに波形が表示されます",
                fill="#6b7280",
            )
            return

        result = self.result
        plan = result.plan
        duration = plan.duration or result.levels.duration or 1.0
        bottom = height - 22
        top = 10

        def x_of(seconds: float) -> float:
            return max(0.0, min(width, seconds / duration * width))

        for span in plan.cut:
            canvas.create_rectangle(
                x_of(span.start), 0, x_of(span.end), bottom,
                fill="#3a1f22", outline="",
            )
        for span in plan.keep:
            canvas.create_rectangle(
                x_of(span.start), 0, x_of(span.end), bottom,
                fill="#123321", outline="",
            )

        levels = result.levels.envelope(max(1, int(width)), floor_db=-70.0)
        if levels:
            center = (top + bottom) / 2
            scale = (bottom - top) / 2 / 70.0
            for x, value in enumerate(levels):
                amplitude = max(0.0, min(70.0, value + 70.0)) * scale
                if amplitude < 0.5:
                    continue
                canvas.create_line(x, center - amplitude, x, center + amplitude,
                                   fill="#7dd3fc")

        threshold = plan.threshold_db
        y = bottom - max(0.0, min(70.0, threshold + 70.0)) / 70.0 * (bottom - top)
        canvas.create_line(0, y, width, y, fill="#fbbf24", dash=(4, 4))
        canvas.create_text(6, y - 8, text=f"{threshold:.0f} dBFS", anchor="w",
                           fill="#fbbf24")

        step = self._tick_step(duration, width)
        tick = 0.0
        while tick <= duration:
            x = x_of(tick)
            canvas.create_line(x, bottom, x, bottom + 5, fill="#4b5563")
            canvas.create_text(x + 2, bottom + 12, text=_fmt_time(tick), anchor="w",
                               fill="#9ca3af")
            tick += step
        canvas.create_line(0, bottom, width, bottom, fill="#4b5563")

    @staticmethod
    def _tick_step(duration: float, width: float) -> float:
        candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
        target = duration / max(1.0, width / 90.0)
        for candidate in candidates:
            if candidate >= target:
                return candidate
        return candidates[-1]


def main(initial_file: Optional[str] = None) -> None:
    from .dnd import create_root
    root = create_root()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = SilenceCutApp(root)
    if initial_file:
        root.after(150, lambda: (app.load_file(initial_file), app.analyze()))
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
