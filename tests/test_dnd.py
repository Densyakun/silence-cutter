import tkinter
import unittest
from pathlib import Path

from silence_cut.dnd import (
    FileDropTarget,
    available,
    create_root,
    enable_file_drop,
    parse_drop_data,
)


class DropDataTest(unittest.TestCase):
    def test_parses_tcl_list_with_spaces(self):
        interpreter = tkinter.Tcl()
        data = "{C:\\path with space\\a.mp4} {C:\\b.mp4}"
        self.assertEqual(
            parse_drop_data(interpreter, data),
            ["C:\\path with space\\a.mp4", "C:\\b.mp4"],
        )

    def test_empty_data(self):
        self.assertEqual(parse_drop_data(tkinter.Tcl(), ""), [])


@unittest.skipUnless(available(), "tkinterdnd2 が必要です")
class FileDropTargetTest(unittest.TestCase):
    def test_drop_callback_receives_existing_files(self):
        try:
            root = create_root()
        except tkinter.TclError as exc:  # pragma: no cover - 画面のない環境
            self.skipTest(str(exc))
        root.withdraw()
        received = []
        target = None
        try:
            target = enable_file_drop(root, received.extend)
            self.assertIsNotNone(target)

            class Event:
                data = "{" + str(Path(__file__).resolve()) + "}"

            target._on_drop(Event())
            self.assertEqual(received, [str(Path(__file__).resolve())])
        finally:
            if target is not None:
                target.detach()
            root.destroy()

    def test_detach_is_safe(self):
        try:
            root = create_root()
        except tkinter.TclError as exc:  # pragma: no cover - 画面のない環境
            self.skipTest(str(exc))
        root.withdraw()
        try:
            target = enable_file_drop(root, lambda paths: None)
            self.assertIsNotNone(target)
            target.detach()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
