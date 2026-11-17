import io
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from silence_cut.detect import CutPlan, Span
from silence_cut.media import MediaError, measure_levels, probe
from silence_cut.pipeline import analyze_file
from silence_cut.render import build_command, render
from silence_cut.settings import AnalysisSettings

SAMPLE_RATE = 16000


def _tone(seconds, frequency=440.0, amplitude=0.3):
    frames = bytearray()
    for index in range(int(SAMPLE_RATE * seconds)):
        value = amplitude * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE)
        frames += struct.pack("<h", int(value * 32767))
    return bytes(frames)


def _silence(seconds):
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg / ffprobe が必要です")
class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "sample.wav"
        with wave.open(str(self.path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(_tone(0.6) + _silence(1.2) + _tone(0.6))

    def tearDown(self):
        self.folder.cleanup()

    def test_probe_reads_audio_metadata(self):
        info = probe(self.path)
        self.assertTrue(info.has_audio)
        self.assertFalse(info.has_video)
        self.assertAlmostEqual(info.duration, 2.4, places=1)

    def test_measure_levels_marks_silence(self):
        settings = AnalysisSettings(frame_ms=20.0)
        levels = measure_levels(self.path, settings)
        self.assertAlmostEqual(levels.duration, 2.4, places=1)
        middle = levels.db[len(levels.db) // 2]
        self.assertLess(middle, -60.0)

    def test_analyze_file_builds_plan(self):
        settings = AnalysisSettings(
            detection="silence", threshold_db=-45.0,
            min_silence=0.5, min_speech=0.15,
            pad_start=0.0, pad_end=0.0,
        )
        result = analyze_file(self.path, settings)
        self.assertEqual(len(result.plan.keep), 2)
        self.assertEqual(len(result.plan.cut), 1)
        self.assertAlmostEqual(result.plan.cut[0].duration, 1.2, places=1)
        payload = result.to_dict()
        self.assertIn("timeline", payload)
        self.assertTrue(payload["source"]["has_audio"])

    def test_analyze_file_speech_mode(self):
        settings = AnalysisSettings(
            detection="speech", min_silence=0.3, min_speech=0.1,
            pad_start=0.0, pad_end=0.0, hangover=0.05,
        )
        result = analyze_file(self.path, settings)
        self.assertTrue(result.levels.has_spectral_features)
        self.assertEqual(result.levels.sample_rate, 8000)
        self.assertEqual(len(result.levels.zcr), len(result.levels.db))
        self.assertEqual(result.plan.settings.detection, "speech")
        self.assertEqual(len(result.plan.keep), 2)
        self.assertIn("noise_db", result.plan.summary())

    def test_render_cuts_silence(self):
        settings = AnalysisSettings(
            detection="silence", threshold_db=-45.0,
            min_silence=0.5, min_speech=0.15,
            pad_start=0.0, pad_end=0.0,
        )
        result = analyze_file(self.path, settings)
        target = Path(self.folder.name) / "out.wav"
        render(result.plan, target)
        self.assertTrue(target.is_file())
        with wave.open(str(target), "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()
        self.assertAlmostEqual(duration, 1.2, places=1)

        command = build_command(result.plan, Path(self.folder.name) / "cmd.wav")
        self.assertIn("-filter_complex", command)
        self.assertIn("concat=n=2", " ".join(command))

    def test_render_uses_filter_script_for_long_graph(self):
        plan = CutPlan(
            source=str(self.path),
            duration=20000.0,
            keep=[
                Span(index * 0.5, index * 0.5 + 0.25)
                for index in range(2000)
            ],
            has_video=False,
            has_audio=True,
        )
        target = Path(self.folder.name) / "long.wav"
        captured = {}

        class FakeProcess:
            returncode = 0

            def __init__(self):
                self.stdout = io.StringIO("out_time_us=1000000\nprogress=end\n")
                self.stderr = io.StringIO("")

            def wait(self):
                return 0

            def poll(self):
                return 0

        def fake_popen(command, **kwargs):
            captured["command"] = command
            index = command.index("-filter_complex_script")
            captured["script_path"] = Path(command[index + 1])
            captured["script"] = captured["script_path"].read_text(encoding="utf-8")
            return FakeProcess()

        with mock.patch("silence_cut.render.subprocess.Popen", fake_popen):
            render(plan, target)

        self.assertIn("-filter_complex_script", captured["command"])
        self.assertNotIn("-filter_complex", captured["command"])
        self.assertGreater(len(captured["script"]), 32767)
        self.assertIn("atrim=", captured["script"])
        self.assertFalse(captured["script_path"].exists())

    def test_render_rejects_empty_plan(self):
        settings = AnalysisSettings(detection="silence", threshold_db=-45.0, min_silence=0.5)
        result = analyze_file(self.path, settings)
        result.plan.keep = []
        with self.assertRaises(ValueError):
            render(result.plan, Path(self.folder.name) / "empty.wav")

    def test_missing_stream_raises(self):
        with self.assertRaises(MediaError):
            probe(Path(self.folder.name) / "missing.mp4")


if __name__ == "__main__":
    unittest.main()
