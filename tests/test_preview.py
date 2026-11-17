import shutil
import unittest

from silence_cut.detect import CutPlan, Span
from silence_cut.preview import PreviewSession, build_preview_command

FFMPEG = shutil.which("ffmpeg")
FFPLAY = shutil.which("ffplay")


def make_plan() -> CutPlan:
    return CutPlan(
        source="sample.mp4",
        duration=4.0,
        keep=[Span(0.0, 1.0), Span(3.0, 4.0)],
        fps=30.0,
        has_video=True,
        has_audio=True,
        width=1920,
        height=1080,
        audio_sample_rate=48000,
    )


class PreviewSessionTest(unittest.TestCase):
    def test_seek_while_paused_updates_position(self):
        session = PreviewSession(make_plan())
        session.seek(1.25)
        self.assertAlmostEqual(session.position, 1.25)
        self.assertFalse(session.is_playing)
        session.close()

    def test_seek_while_suspended_is_deferred_until_play(self):
        session = PreviewSession(make_plan())
        session._suspended = True
        session.seek(1.25)
        self.assertAlmostEqual(session.position, 1.25)
        self.assertTrue(session.has_pending_seek)
        self.assertFalse(session.is_playing)
        session._suspended = False
        session.close()


@unittest.skipUnless(FFMPEG and FFPLAY, "ffmpeg / ffplay が必要です")
class PreviewCommandTest(unittest.TestCase):
    def build(self, plan: CutPlan, start: float):
        return build_preview_command(
            plan, start, ffmpeg=FFMPEG, ffplay=FFPLAY,
            script_path="preview.ffgraph",
        )

    def test_starts_inside_the_requested_span(self):
        command = self.build(make_plan(), 1.5)
        self.assertAlmostEqual(command.seek_source, 3.5)
        self.assertIn("trim=start=0.000000:end=0.500000", command.filter_graph)
        self.assertIn("atrim=start=0.000000:end=0.500000", command.filter_graph)
        self.assertIn("concat=n=1:v=1:a=1", command.filter_graph)

    def test_earlier_spans_are_not_processed(self):
        plan = make_plan()
        command = self.build(plan, 0.5)
        self.assertAlmostEqual(command.seek_source, 0.5)
        self.assertIn("trim=start=0.000000:end=0.500000", command.filter_graph)
        self.assertIn("trim=start=2.500000:end=3.500000", command.filter_graph)
        self.assertIn("concat=n=2:v=1:a=1", command.filter_graph)

    def test_uses_streaming_and_filter_script(self):
        command = self.build(make_plan(), 0.0)
        self.assertIn("-filter_complex_script", command.ffmpeg)
        self.assertIn("-ss", command.ffmpeg)
        self.assertIn("pipe:1", command.ffmpeg)
        self.assertIn("pipe:0", command.ffplay)
        self.assertIn("-autoexit", command.ffplay)

    def test_audio_only_preview_hides_video(self):
        plan = make_plan()
        plan.has_video = False
        command = self.build(plan, 0.0)
        self.assertIn("atrim=", command.filter_graph)
        self.assertNotIn("[0:v:0]trim=", command.filter_graph)
        self.assertIn("-nodisp", command.ffplay)


if __name__ == "__main__":
    unittest.main()
