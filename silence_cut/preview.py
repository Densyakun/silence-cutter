from __future__ import annotations

import ctypes
import io
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .detect import CutPlan
from .media import MediaError, PathLike, find_ffmpeg

PREVIEW_WIDTH = 640
PREVIEW_CRF = 30


def find_ffplay(
    explicit: Optional[PathLike] = None,
    *,
    ffmpeg: Optional[PathLike] = None,
) -> str:
    """ffplay を探す。明示指定 → ffmpeg と同じフォルダ → PATH の順。"""
    candidates: List[str] = []
    if explicit:
        candidates.append(str(explicit))
    if ffmpeg:
        folder = Path(ffmpeg).resolve().parent
        names = ("ffplay.exe", "ffplay") if os.name == "nt" else ("ffplay",)
        candidates.extend(str(folder / name) for name in names)
    candidates.append("ffplay")
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
        found = shutil.which(candidate)
        if found:
            return found
    raise MediaError(
        "ffplay が見つかりません。FFmpeg をインストールして PATH に追加するか、"
        "ffmpeg と同じフォルダに ffplay を置いてください。"
    )


@dataclass(frozen=True)
class PreviewCommand:
    ffmpeg: List[str]
    ffplay: List[str]
    filter_graph: str
    start_time: float
    seek_source: float


def _preview_spans(plan: CutPlan, start_time: float) -> List[Tuple[object, float, float]]:
    spans = []
    cursor = 0.0
    for span in plan.keep:
        length = span.duration
        virtual_start = cursor
        virtual_end = cursor + length
        if virtual_end > start_time + 1e-9:
            spans.append((span, virtual_start, virtual_end))
        cursor = virtual_end
    return spans


def build_preview_command(
    plan: CutPlan,
    start_time: float,
    *,
    ffmpeg: Optional[PathLike] = None,
    ffplay: Optional[PathLike] = None,
    script_path: PathLike = "preview.ffgraph",
) -> PreviewCommand:
    """指定した再生位置から始まるプレビュー用の FFmpeg / ffplay コマンドを作る。

    全体を先に書き出すのではなく、``-ss`` で素材の必要な位置へシークし、
    そこから先の残す区間だけを低画質でストリーム変換する。
    """
    if not plan.keep:
        raise ValueError("残す区間がありません。しきい値や最小無音長を調整してください。")

    duration = plan.output_duration
    start = max(0.0, min(float(start_time), duration))
    spans = _preview_spans(plan, start)
    if not spans:
        start = 0.0
        spans = _preview_spans(plan, 0.0)

    first_span, first_virtual_start, _ = spans[0]
    offset = max(0.0, start - first_virtual_start)
    seek_source = min(first_span.end, first_span.start + offset)

    width = plan.width or PREVIEW_WIDTH
    width = max(2, min(PREVIEW_WIDTH, width))
    width -= width % 2

    chains: List[str] = []
    video_labels: List[str] = []
    audio_labels: List[str] = []
    for index, (span, _, _) in enumerate(spans):
        trim_start = max(0.0, span.start - seek_source)
        trim_end = span.end - seek_source
        if trim_end <= trim_start + 1e-9:
            continue
        start_text = f"{trim_start:.6f}"
        end_text = f"{trim_end:.6f}"
        if plan.has_video:
            chains.append(
                f"[0:v:0]trim=start={start_text}:end={end_text},"
                f"setpts=PTS-STARTPTS,"
                f"scale={width}:-2:flags=fast_bilinear[v{index}]"
            )
            video_labels.append(f"[v{index}]")
        if plan.has_audio:
            chains.append(
                f"[0:a:0]atrim=start={start_text}:end={end_text},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
            audio_labels.append(f"[a{index}]")

    count = min(len(video_labels), len(audio_labels)) if (
        plan.has_video and plan.has_audio
    ) else max(len(video_labels), len(audio_labels))
    if count <= 0:
        raise ValueError("プレビューできる区間がありません。")
    if plan.has_video and plan.has_audio:
        count = len(video_labels)
        inputs = "".join(video_labels[i] + audio_labels[i] for i in range(count))
        chains.append(f"{inputs}concat=n={count}:v=1:a=1[vcat][acat]")
        video_out, audio_out = "[vcat]", "[acat]"
    elif plan.has_video:
        inputs = "".join(video_labels)
        chains.append(f"{inputs}concat=n={len(video_labels)}:v=1:a=0[vcat]")
        video_out, audio_out = "[vcat]", None
    else:
        inputs = "".join(audio_labels)
        chains.append(f"{inputs}concat=n={len(audio_labels)}:v=0:a=1[acat]")
        video_out, audio_out = None, "[acat]"

    executable = find_ffmpeg(ffmpeg)
    player = find_ffplay(ffplay, ffmpeg=executable)
    ffmpeg_command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seek_source:.6f}",
        "-i", plan.source,
        "-filter_complex_script", str(script_path),
    ]
    if video_out:
        ffmpeg_command += [
            "-map", video_out,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-crf", str(PREVIEW_CRF), "-pix_fmt", "yuv420p",
        ]
    if audio_out:
        ffmpeg_command += ["-map", audio_out, "-c:a", "aac", "-b:a", "128k"]
    ffmpeg_command += ["-f", "matroska", "pipe:1"]

    ffplay_command = [
        player, "-hide_banner", "-loglevel", "info", "-autoexit",
        "-window_title", "Silence Cut - プレビュー",
    ]
    if plan.has_video:
        # 実際に再生されている位置を stderr の pts_time から取得する
        ffplay_command += ["-vf", "showinfo"]
    else:
        ffplay_command += ["-nodisp", "-af", "ashowinfo"]
    ffplay_command += ["-i", "pipe:0"]

    return PreviewCommand(
        ffmpeg=ffmpeg_command,
        ffplay=ffplay_command,
        filter_graph=";".join(chains),
        start_time=start,
        seek_source=seek_source,
    )


class PreviewSession:
    """プレビューを非同期にストリーミング再生するセッション。

    FFmpeg が「現在位置から先」の残す区間だけを低画質で変換し、ffplay が
    その出力をリアルタイムに再生する。全体の書き出しを待たないため、
    長い素材でもすぐに再生を開始できる。シーク時は FFmpeg を新しい位置で
    起動し直すので、その位置以降だけが処理される。
    """

    def __init__(
        self,
        plan: CutPlan,
        *,
        ffmpeg: Optional[PathLike] = None,
        ffplay: Optional[PathLike] = None,
        video_parent_hwnd: Optional[int] = None,
    ) -> None:
        self.plan = plan
        self.duration = max(0.0, plan.output_duration)
        self._ffmpeg = ffmpeg
        self._ffplay = ffplay
        self._video_parent_hwnd = int(video_parent_hwnd or 0)
        self._video_size: Tuple[int, int] = (0, 0)
        self._lock = threading.RLock()
        self._position = 0.0
        self._playing = False
        self._suspended = False
        self._pending_restart = False
        self._closed = False
        self._error: Optional[str] = None
        self._generation = 0
        self._runs: List["_PreviewRun"] = []
        self._embed_pid = 0
        self._player_hwnd = 0

    @property
    def position(self) -> float:
        with self._lock:
            return self._position

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    @property
    def has_pending_seek(self) -> bool:
        with self._lock:
            return self._pending_restart

    def play(self, position: Optional[float] = None) -> None:
        with self._lock:
            if self._closed:
                return
            if position is not None:
                self._position = self._clamp(position)
                self._pending_restart = False
                self._restart_locked()
                return
            if self._suspended and self._runs and not self._pending_restart:
                self._resume_runs_locked()
                self._suspended = False
                self._playing = True
                return
            if self._position >= self.duration - 1e-6:
                self._position = 0.0
            self._pending_restart = False
            self._restart_locked()

    def pause(self) -> None:
        with self._lock:
            if not self._playing:
                return
            if self._runs and self._suspend_runs_locked():
                self._playing = False
                self._suspended = True
                return
            self._teardown_locked()
            self._playing = False
            self._suspended = False

    def seek(self, position: float) -> None:
        with self._lock:
            if self._closed:
                return
            new_position = self._clamp(position)
            if self._suspended:
                # 一時停止中のフレームは消さず、再生時に新しい位置から作り直す
                self._position = new_position
                self._pending_restart = True
                return
            was_playing = self._playing
            self._teardown_locked()
            self._position = new_position
            self._pending_restart = False
            if was_playing and self._position < self.duration - 1e-6:
                self._restart_locked()
            else:
                self._playing = False

    def stop(self) -> None:
        with self._lock:
            self._teardown_locked()
            self._playing = False
            self._suspended = False
            self._pending_restart = False
            self._position = 0.0
            self._error = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._teardown_locked()
            self._playing = False
            self._suspended = False
            self._pending_restart = False

    def resize_player(self, width: int, height: int) -> None:
        with self._lock:
            if width > 1 and height > 1:
                self._video_size = (int(width), int(height))
                if self._player_hwnd:
                    _move_player_window(self._player_hwnd, width, height)

    def poll(self) -> None:
        """GUI の定期処理から呼ぶ。ffplay のウィンドウを埋め込む。"""
        if os.name != "nt":
            return
        with self._lock:
            if not self._embed_pid or not self._video_parent_hwnd:
                return
            hwnd = _find_player_window(self._embed_pid)
            if not hwnd:
                self._embed_attempts -= 1
                if self._embed_attempts <= 0:
                    self._embed_pid = 0
                return
            _embed_player_window(hwnd, self._video_parent_hwnd)
            self._player_hwnd = hwnd
            self._embed_pid = 0
            self._embed_attempts = 0
            width, height = self._video_size
            if width > 1 and height > 1:
                _move_player_window(hwnd, width, height)

    # ------------------------------------------------------------- internals
    def _clamp(self, position: float) -> float:
        return max(0.0, min(float(position), self.duration))

    def _teardown_locked(self) -> None:
        self._generation += 1
        for run in self._runs:
            _terminate(run.ffplay)
            _terminate(run.ffmpeg)
        self._runs.clear()
        self._embed_pid = 0
        self._embed_attempts = 0
        self._player_hwnd = 0
        self._suspended = False

    def _restart_locked(self) -> None:
        self._teardown_locked()
        self._error = None
        self._playing = True
        self._suspended = False
        self._pending_restart = False
        generation = self._generation
        try:
            self._start_locked(generation)
        except Exception as exc:
            self._playing = False
            self._error = str(exc)
            raise

    def _start_locked(self, generation: int) -> None:
        handle, script_name = tempfile.mkstemp(
            prefix="silence-cut-preview-", suffix=".ffgraph",
        )
        os.close(handle)
        script_path = Path(script_name)
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        ffmpeg_proc: Optional[subprocess.Popen] = None
        ffplay_proc: Optional[subprocess.Popen] = None
        try:
            command = build_preview_command(
                self.plan, self._position,
                ffmpeg=self._ffmpeg, ffplay=self._ffplay,
                script_path=script_path,
            )
            script_path.write_text(command.filter_graph, encoding="utf-8")
            ffmpeg_proc = subprocess.Popen(
                command.ffmpeg,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=creationflags,
            )
            if ffmpeg_proc.stdout is None:  # pragma: no cover - Popen 仕様
                raise MediaError("プレビュー用のパイプを作成できませんでした。")
            ffplay_proc = subprocess.Popen(
                command.ffplay,
                stdin=ffmpeg_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
            ffmpeg_proc.stdout.close()
        except Exception:
            if ffmpeg_proc is not None:
                _terminate(ffmpeg_proc)
                try:
                    ffmpeg_proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        assert ffmpeg_proc is not None and ffplay_proc is not None

        run = _PreviewRun(
            generation=generation,
            ffmpeg=ffmpeg_proc,
            ffplay=ffplay_proc,
            script=script_path,
            start_time=command.start_time,
        )
        self._runs.append(run)
        self._player_hwnd = 0
        self._embed_pid = ffplay_proc.pid if self._video_parent_hwnd else 0
        self._embed_attempts = 50
        threading.Thread(
            target=self._read_ffmpeg_errors, args=(run,), daemon=True,
        ).start()
        threading.Thread(
            target=self._read_player_position, args=(run,), daemon=True,
        ).start()
        threading.Thread(
            target=self._monitor, args=(run,), daemon=True,
        ).start()

    def _suspend_runs_locked(self) -> bool:
        if not self._runs:
            return False
        ok = True
        for run in self._runs:
            if not _suspend_process(run.ffplay):
                ok = False
            if not _suspend_process(run.ffmpeg):
                ok = False
        return ok

    def _resume_runs_locked(self) -> None:
        for run in self._runs:
            _resume_process(run.ffmpeg)
            _resume_process(run.ffplay)

    def _read_ffmpeg_errors(self, run: "_PreviewRun") -> None:
        stream = run.ffmpeg.stderr
        if stream is None:  # pragma: no cover - Popen 仕様
            return
        text_stream = io.TextIOWrapper(stream, encoding="utf-8", errors="replace")
        try:
            for line in text_stream:
                line = line.strip()
                if line:
                    run.last_message = line
        except (ValueError, OSError):
            pass

    def _read_player_position(self, run: "_PreviewRun") -> None:
        stream = run.ffplay.stderr
        if stream is None:  # pragma: no cover - Popen 仕様
            return
        text_stream = io.TextIOWrapper(stream, encoding="utf-8", errors="replace")
        try:
            for line in text_stream:
                marker = line.find("pts_time:")
                if marker < 0:
                    continue
                token = line[marker + len("pts_time:"):].strip().split(" ", 1)[0]
                try:
                    seconds = float(token)
                except ValueError:
                    continue
                with self._lock:
                    if run.generation == self._generation:
                        self._position = min(
                            self.duration, run.start_time + max(0.0, seconds),
                        )
        except (ValueError, OSError):
            pass

    def _monitor(self, run: "_PreviewRun") -> None:
        try:
            run.ffplay.wait()
        except OSError:  # pragma: no cover - プロセス終了時の競合
            pass
        if run.ffmpeg.poll() is None:
            _terminate(run.ffmpeg)
        try:
            run.ffmpeg.wait(timeout=2)
        except subprocess.TimeoutExpired:  # pragma: no cover - 異常時
            try:
                run.ffmpeg.kill()
            except OSError:
                pass

        with self._lock:
            if run.generation == self._generation:
                self._embed_pid = 0
                self._embed_attempts = 0
                self._player_hwnd = 0
                self._suspended = False
            if run.generation == self._generation and self._playing:
                self._playing = False
                at_end = self._position >= self.duration - 1.5
                if at_end:
                    self._position = self.duration
                    self._pending_restart = False
                if (
                    not at_end
                    and run.ffplay.returncode not in (0, None)
                    and run.last_message
                ):
                    self._error = run.last_message
            try:
                self._runs.remove(run)
            except ValueError:
                pass

        try:
            run.script.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class _PreviewRun:
    generation: int
    ffmpeg: subprocess.Popen
    ffplay: subprocess.Popen
    script: Path
    start_time: float
    last_message: str = ""


def _terminate(process: subprocess.Popen) -> None:
    try:
        if process.poll() is not None:
            return
        if os.name != "nt":
            import signal
            try:
                os.kill(process.pid, signal.SIGCONT)
            except OSError:
                pass
        process.terminate()
    except OSError:
        pass


def _suspend_process(process: subprocess.Popen) -> bool:
    try:
        if process.poll() is not None:
            return False
        if os.name == "nt":
            import ctypes.wintypes as wintypes
            ntdll = ctypes.windll.ntdll
            ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
            ntdll.NtSuspendProcess.restype = ctypes.c_long
            handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
            return ntdll.NtSuspendProcess(handle) == 0
        import signal
        os.kill(process.pid, signal.SIGSTOP)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _resume_process(process: subprocess.Popen) -> bool:
    try:
        if process.poll() is not None:
            return False
        if os.name == "nt":
            import ctypes.wintypes as wintypes
            ntdll = ctypes.windll.ntdll
            ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
            return ntdll.NtResumeProcess(handle) == 0
        import signal
        os.kill(process.pid, signal.SIGCONT)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _find_player_window(pid: int) -> int:
    if os.name != "nt" or not pid:
        return 0
    import ctypes.wintypes as wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
    )
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    found: List[int] = []

    def callback(hwnd, _lparam):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value != pid:
            return True
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, 128)
        if "SDL" in name.value:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else 0


def _embed_player_window(hwnd: int, parent: int) -> None:
    if os.name != "nt":
        return
    import ctypes.wintypes as wintypes
    user32 = ctypes.windll.user32
    gwl_style = -16
    gwl_exstyle = -20
    ws_child = 0x40000000
    ws_visible = 0x10000000
    ws_popup = 0x80000000
    ws_caption = 0x00C00000
    ws_thickframe = 0x00040000
    ws_sysmenu = 0x00080000
    ws_ex_noactivate = 0x08000000
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [
        wintypes.HWND, ctypes.c_int, ctypes.c_long,
    ]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    style = int(user32.GetWindowLongW(wintypes.HWND(hwnd), gwl_style) or 0)
    style &= ~(ws_popup | ws_caption | ws_thickframe | ws_sysmenu)
    style |= ws_child | ws_visible
    user32.SetWindowLongW(wintypes.HWND(hwnd), gwl_style, style)
    exstyle = int(user32.GetWindowLongW(wintypes.HWND(hwnd), gwl_exstyle) or 0)
    user32.SetWindowLongW(
        wintypes.HWND(hwnd), gwl_exstyle, exstyle | ws_ex_noactivate,
    )
    user32.SetParent(wintypes.HWND(hwnd), wintypes.HWND(parent))


def _move_player_window(hwnd: int, width: int, height: int) -> None:
    if os.name != "nt" or not hwnd:
        return
    import ctypes.wintypes as wintypes
    user32 = ctypes.windll.user32
    user32.MoveWindow.argtypes = [
        wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.BOOL,
    ]
    user32.MoveWindow.restype = wintypes.BOOL
    user32.MoveWindow(
        wintypes.HWND(hwnd), 0, 0,
        max(1, int(width)), max(1, int(height)), True,
    )
