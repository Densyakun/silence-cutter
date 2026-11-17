from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, List, Optional

try:  # tkinterdnd2 が無い環境でも GUI 自体は起動できるようにする
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
except Exception:  # pragma: no cover - 依存パッケージ未導入時
    COPY = None  # type: ignore[assignment]
    DND_FILES = None  # type: ignore[assignment]
    TkinterDnD = None  # type: ignore[assignment]


def available() -> bool:
    return TkinterDnD is not None and DND_FILES is not None


def create_root() -> tk.Tk:
    """ドラッグ＆ドロップ対応の Tk ルートを作る。

    tkinterdnd2 が使えない場合は通常の ``tk.Tk()`` にフォールバックする。
    """
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:  # pragma: no cover - Tcl パッケージ読込失敗時
            pass
    return tk.Tk()


def parse_drop_data(widget, data: str) -> List[str]:
    """tkdnd の ``<<Drop>>`` イベントデータ（Tcl リスト）をパス一覧にする。"""
    if not data:
        return []
    try:
        items = [str(item) for item in widget.tk.splitlist(data)]
    except tk.TclError:
        items = [data]
    return [item for item in items if item]


class FileDropTarget:
    def __init__(
        self,
        callback: Callable[[List[str]], None],
    ) -> None:
        self.callback = callback
        self._widgets: List[tk.Misc] = []
        self._root = None

    def attach(self, widget) -> None:
        self._root = widget
        for node in _walk(widget):
            try:
                node.drop_target_register(DND_FILES)
                node.dnd_bind("<<Drop>>", self._on_drop)
                self._widgets.append(node)
            except Exception:
                continue

    def _on_drop(self, event) -> Optional[str]:
        widget = getattr(event, "widget", None) or self._root
        paths = parse_drop_data(widget, str(getattr(event, "data", "")))
        files = [path for path in paths if Path(path).is_file()]
        if files:
            self.callback(files)
        return COPY

    def detach(self) -> None:
        for widget in self._widgets:
            try:
                widget.dnd_bind("<<Drop>>", "")
            except tk.TclError:
                pass
            try:
                widget.drop_target_unregister()
            except tk.TclError:
                pass
        self._widgets.clear()


def _walk(widget):
    yield widget
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return
    for child in children:
        yield from _walk(child)


def enable_file_drop(
    widget,
    callback: Callable[[List[str]], None],
) -> Optional[FileDropTarget]:
    """ウィンドウ全体（子ウィジェットを含む）をファイルのドロップ先にする。"""
    if not available():
        return None
    target = FileDropTarget(callback)
    target.attach(widget)
    if not target._widgets:
        return None
    return target
