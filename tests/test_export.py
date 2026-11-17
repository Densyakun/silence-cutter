import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from silence_cut.detect import build_plan
from silence_cut.export import (
    to_csv,
    to_edl,
    to_exo,
    to_fcpxml,
    to_json,
    to_xmeml,
    write_plan,
)
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
        AnalysisSettings(detection="silence", threshold_db=-45.0,
                         min_silence=0.5, min_speech=0.15,
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
        self.assertEqual(data["version"], 2)
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

    def test_edl_uses_drop_frame_for_ntsc(self):
        self.plan.fps = 29.97
        text = to_edl(self.plan)
        self.assertIn("FCM: DROP FRAME", text)
        self.assertNotIn("FCM: NON-DROP FRAME", text)

    def test_exo_lists_video_and_audio_objects(self):
        text = to_exo(self.plan)
        self.assertIn("[exedit]", text)
        self.assertIn("rate=30", text)
        self.assertIn("scale=1", text)
        self.assertIn("length=60", text)
        self.assertEqual(text.count("_name=動画ファイル"), 2)
        self.assertEqual(text.count("_name=音声ファイル"), 2)
        self.assertIn("start=1\nend=30", text)
        self.assertIn("start=31\nend=60", text)
        self.assertIn("再生位置=1", text)
        self.assertIn("再生位置=91", text)
        self.assertIn("動画ファイルと連携=1", text)
        self.assertIn("layer=2", text)
        self.assertIn("file=", text)
        self.assertIn("sample.mp4", text)

    def test_exo_audio_only_uses_file_directly(self):
        self.plan.has_video = False
        text = to_exo(self.plan)
        self.assertNotIn("_name=動画ファイル", text)
        self.assertEqual(text.count("_name=音声ファイル"), 2)
        self.assertIn("動画ファイルと連携=0", text)
        self.assertIn("layer=1", text)

    def test_write_plan_by_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix, expected in (
                (".json", '"version"'),
                (".csv", "action"),
                (".edl", "TITLE"),
                (".exo", "[exedit]"),
                (".xml", "<xmeml"),
                (".fcpxml", "<fcpxml"),
            ):
                target = Path(folder) / f"plan{suffix}"
                write_plan(self.plan, target)
                if suffix == ".exo":
                    text = target.read_bytes().decode("cp932")
                    self.assertIn("\r\n", text)
                else:
                    text = target.read_text(encoding="utf-8-sig")
                self.assertIn(expected, text)

    def test_fcpxml_lists_keep_segments(self):
        text = to_fcpxml(self.plan)
        root = ET.fromstring(text)
        self.assertEqual(root.tag, "fcpxml")
        self.assertEqual(root.get("version"), "1.9")
        clips = root.findall(".//spine/asset-clip")
        self.assertEqual(len(clips), 2)
        self.assertEqual(len(root.findall(".//resources/asset")), 1)
        self.assertEqual(root.find(".//media-rep").get("src").split("/")[-1], "sample.mp4")
        self.assertEqual(root.find(".//sequence").get("duration"), "60/30s")
        self.assertEqual(root.find(".//asset").get("duration"), "120/30s")

    def test_xmeml_lists_keep_segments(self):
        text = to_xmeml(self.plan)
        root = ET.fromstring(text)
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.find(".//sequence/duration").text, "60")
        video_clips = root.findall(".//media/video/track/clipitem")
        audio_clips = root.findall(".//media/audio/track/clipitem")
        self.assertEqual(len(video_clips), 2)
        self.assertEqual(len(audio_clips), 2)
        self.assertEqual(video_clips[1].find("in").text, "90")
        self.assertEqual(video_clips[1].find("out").text, "120")
        path_url = video_clips[0].find("file/pathurl").text
        self.assertTrue(path_url.startswith("file:"))
        self.assertTrue(path_url.endswith("sample.mp4"))

    def test_write_plan_rejects_unknown_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                write_plan(self.plan, Path(folder) / "plan.xyz")


if __name__ == "__main__":
    unittest.main()
