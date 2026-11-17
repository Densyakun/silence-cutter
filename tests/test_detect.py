import unittest

from silence_cut.detect import build_plan
from silence_cut.media import AudioLevels
from silence_cut.settings import AnalysisSettings


def levels_from(pattern, frame_ms=20.0):
    db = []
    for value, seconds in pattern:
        db.extend([value] * int(round(seconds * 1000.0 / frame_ms)))
    return AudioLevels(
        sample_rate=16000,
        frame_ms=frame_ms,
        db=db,
        duration=len(db) * frame_ms / 1000.0,
    )


BASE = AnalysisSettings(
    detection="silence",
    threshold_db=-45.0,
    min_silence=0.5,
    min_speech=0.15,
    pad_start=0.0,
    pad_end=0.0,
)


class BuildPlanTest(unittest.TestCase):
    def test_default_detection_is_speech(self):
        self.assertEqual(AnalysisSettings().detection, "speech")

    def test_speech_mode_does_not_validate_silence_threshold(self):
        AnalysisSettings(detection="speech", threshold_db=10.0).validated()

    def test_detects_two_speech_spans(self):
        levels = levels_from([(-20, 1.0), (-80, 1.0), (-20, 1.0)])
        plan = build_plan(levels, BASE)
        self.assertEqual(len(plan.keep), 2)
        self.assertEqual(len(plan.cut), 1)
        self.assertAlmostEqual(plan.keep[0].start, 0.0, places=1)
        self.assertAlmostEqual(plan.keep[0].end, 1.0, places=1)
        self.assertAlmostEqual(plan.keep[1].start, 2.0, places=1)
        self.assertAlmostEqual(plan.keep[1].end, 3.0, places=1)
        self.assertAlmostEqual(plan.cut[0].duration, 1.0, places=1)
        self.assertAlmostEqual(plan.removed_ratio, 1 / 3, places=2)

    def test_short_noise_blip_does_not_split_silence(self):
        levels = levels_from([(-20, 1.0), (-80, 1.5), (-20, 1.0)])
        # 0.04 秒だけのクリック音を無音区間の中央に差し込む
        start = int(1.0 * 50) + int(0.7 * 50)
        for index in range(start, start + 2):
            levels.db[index] = -20.0
        plan = build_plan(levels, BASE)
        self.assertEqual(len(plan.cut), 1)
        self.assertAlmostEqual(plan.cut[0].duration, 1.5, places=1)

    def test_short_silence_is_kept(self):
        levels = levels_from([(-20, 1.0), (-80, 0.2), (-20, 1.0)])
        plan = build_plan(levels, BASE)
        self.assertEqual(len(plan.keep), 1)
        self.assertEqual(plan.cut, [])
        self.assertAlmostEqual(plan.keep[0].end, 2.2, places=1)

    def test_padding_is_applied_and_clamped(self):
        levels = levels_from([(-80, 0.5), (-20, 1.0), (-80, 2.0)])
        settings = BASE.with_(pad_start=0.2, pad_end=0.3)
        plan = build_plan(levels, settings)
        self.assertEqual(len(plan.keep), 1)
        self.assertAlmostEqual(plan.keep[0].start, 0.3, places=1)
        self.assertAlmostEqual(plan.keep[0].end, 1.8, places=1)

    def test_full_silence_produces_no_keep(self):
        levels = levels_from([(-80, 2.0)])
        plan = build_plan(levels, BASE)
        self.assertEqual(plan.keep, [])
        self.assertEqual(len(plan.cut), 1)
        self.assertAlmostEqual(plan.cut[0].duration, 2.0, places=1)

    def test_adaptive_threshold_detects_quiet_speech(self):
        levels = levels_from([(-80, 4.0), (-55, 1.0)])
        settings = BASE.with_(threshold_db=-70.0, adaptive=True, min_silence=0.3)
        plan = build_plan(levels, settings)
        self.assertLess(plan.threshold_db, -60.0)
        self.assertEqual(len(plan.keep), 1)
        self.assertAlmostEqual(plan.keep[0].duration, 1.0, places=1)

    def test_adaptive_threshold_never_cuts_everything(self):
        levels = levels_from([(-20, 9.0), (-80, 1.0)])
        settings = BASE.with_(adaptive=True)
        plan = build_plan(levels, settings)
        self.assertGreater(plan.speech_duration, 8.5)

    def test_timeline_maps_record_positions(self):
        levels = levels_from([(-20, 1.0), (-80, 2.0), (-20, 0.5)])
        plan = build_plan(levels, BASE)
        timeline = plan.timeline()
        self.assertEqual(len(timeline), 2)
        self.assertAlmostEqual(timeline[0]["record_start"], 0.0, places=2)
        self.assertAlmostEqual(timeline[1]["record_start"], 1.0, places=1)
        self.assertAlmostEqual(plan.output_duration, 1.5, places=1)

    def test_empty_levels(self):
        levels = AudioLevels(16000, 20.0, [], 0.0)
        plan = build_plan(levels, BASE)
        self.assertEqual(plan.keep, [])
        self.assertEqual(plan.cut, [])
        self.assertEqual(plan.summary()["cut_segments"], 0)

    def test_invalid_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(levels_from([(-20, 1.0)]), BASE.with_(threshold_db=10.0))


if __name__ == "__main__":
    unittest.main()
