import json
import tempfile
import unittest
from pathlib import Path

from silence_cut.detect import build_plan
from silence_cut.export import to_csv, to_edl, to_json, write_plan
from silence_cut.media import AudioLevels
from silence_cut.settings import AnalysisSettings


def make_plan():
    db = (
        [-20.0] * 50
        + [-80.0] * 100
        + [-20.0] * 50
    )
    levels = AudioLevels(16000, 20.0, db, len(db) * 0.02)
    return build_plan(
        levels,
        AnalysisSettings(threshold_db=-45.0, min_silence=0.5, min_speech=0.15,
                         pad_start=0.0, pad_end=0.0),
        duration=levels.duration,
        source="sample.mp4",
        fps=30.0,
    )


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()

    def test_plan_json_has_common_fields(self):
        data = json.loads(to_json(self.plan))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["source"]["path"], "sample.mp4")
        self.assertEqual(len(data["keep"]), 2)
        self.assertEqual(len(data["cut"]), 1)
        self.assertEqual(len(data["timeline"]), 2)
        self.assertAlmostEqual(data["summary"]["removed_ratio"], 0.5, places=2)
        self.assertIn("settings", data)

    def test_csv_lists_keep_and_cut(self):
        text = to_csv(self.plan)
        lines = [line for line in text.strip().splitlines() if line]
        self.assertEqual(lines[0].split(",")[:2], ["index", "action"])
        self.assertEqual(len(lines), 1 + 2 + 1)
        self.assertIn(",keep,", text)
        self.assertIn(",cut,", text)

    def test_edl_timecode_format(self):
        text = to_edl(self.plan)
        self.assertIn("TITLE: sample", text)
        self.assertIn("FCM: NON-DROP FRAME", text)
        events = [line for line in text.splitlines() if line[:3].isdigit()]
        self.assertEqual(len(events), 4)  # 2 区間 x (映像 + 音声)
        self.assertRegex(events[0], r"^\d{3}  AX       V     C        \d{2}:\d{2}:\d{2}:\d{2} ")
        self.assertIn("* FROM CLIP NAME: sample.mp4", text)

    def test_write_plan_by_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix, expected in ((".json", '"version"'), (".csv", "action"), (".edl", "TITLE")):
                target = Path(folder) / f"plan{suffix}"
                write_plan(self.plan, target)
                text = target.read_text(encoding="utf-8-sig")
                self.assertIn(expected, text)

    def test_write_plan_rejects_unknown_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                write_plan(self.plan, Path(folder) / "plan.xyz")


if __name__ == "__main__":
    unittest.main()
