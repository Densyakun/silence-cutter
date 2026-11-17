import unittest

from silence_cut.detect import build_plan
from silence_cut.media import AudioLevels
from silence_cut.settings import AnalysisSettings
from silence_cut.vad import detect_speech

SPEECH = dict(db=-25.0, zcr=0.06, low_ratio=0.80)
HISS = dict(db=-42.0, zcr=0.55, low_ratio=0.12)
SILENCE = dict(db=-120.0, zcr=0.0, low_ratio=0.0)

VAD_BASE = AnalysisSettings(
    detection="speech",
    min_silence=0.3,
    min_speech=0.1,
    pad_start=0.0,
    pad_end=0.0,
    hangover=0.1,
)


def levels_from_frames(frames, frame_ms=20.0):
    """frames: [(db, zcr, low_ratio, seconds), ...]"""
    db, zcr, low_ratio = [], [], []
    for value_db, value_zcr, value_low, seconds in frames:
        count = int(round(seconds * 1000.0 / frame_ms))
        db.extend([value_db] * count)
        zcr.extend([value_zcr] * count)
        low_ratio.extend([value_low] * count)
    return AudioLevels(
        sample_rate=8000,
        frame_ms=frame_ms,
        db=db,
        duration=len(db) * frame_ms / 1000.0,
        zcr=zcr,
        low_ratio=low_ratio,
    )


def frame(kind, seconds):
    return (kind["db"], kind["zcr"], kind["low_ratio"], seconds)


class SpeechDetectionTest(unittest.TestCase):
    def test_cuts_steady_hiss_and_keeps_speech(self):
        levels = levels_from_frames([
            frame(HISS, 2.0), frame(SPEECH, 1.0), frame(HISS, 2.0),
        ])
        decision = detect_speech(levels, VAD_BASE)
        self.assertFalse(decision.fallback)
        self.assertGreater(decision.noise_db, -60.0)
        plan = build_plan(levels, VAD_BASE)
        self.assertEqual(len(plan.keep), 1)
        # 発話 1.0 秒 + hangover 0.1 秒
        self.assertTrue(1.0 <= plan.keep[0].duration <= 1.2)
        self.assertTrue(3.8 <= plan.silence_duration <= 4.0)
        self.assertEqual(plan.settings.detection, "speech")
        self.assertIsNotNone(plan.noise_db)

    def test_digital_silence_is_cut(self):
        levels = levels_from_frames([
            frame(SPEECH, 0.8), frame(SILENCE, 1.5), frame(SPEECH, 0.8),
        ])
        plan = build_plan(levels, VAD_BASE)
        self.assertEqual(len(plan.keep), 2)
        self.assertEqual(len(plan.cut), 1)
        self.assertAlmostEqual(plan.cut[0].duration, 1.5, places=1)

    def test_short_gap_is_bridged(self):
        levels = levels_from_frames([
            frame(SPEECH, 0.4), frame(SILENCE, 0.1), frame(SPEECH, 0.4),
        ])
        plan = build_plan(levels, VAD_BASE)
        self.assertEqual(len(plan.keep), 1)
        self.assertEqual(plan.cut, [])
        self.assertAlmostEqual(plan.keep[0].duration, 0.9, places=1)

    def test_constant_level_falls_back_without_removing_everything(self):
        levels = levels_from_frames([(-20.0, 0.05, 0.9, 2.0)])
        decision = detect_speech(levels, VAD_BASE)
        self.assertTrue(decision.fallback)
        plan = build_plan(levels, VAD_BASE)
        self.assertGreater(plan.speech_duration, 1.9)

    def test_energy_only_features_still_work(self):
        levels = levels_from_frames([
            frame(HISS, 1.0), frame(SPEECH, 0.6), frame(HISS, 1.0),
        ])
        levels.zcr = []
        levels.low_ratio = []
        plan = build_plan(levels, VAD_BASE)
        self.assertEqual(len(plan.keep), 1)
        self.assertTrue(0.6 <= plan.keep[0].duration <= 0.8)

    def test_external_activity_overrides_vad(self):
        levels = levels_from_frames([frame(HISS, 2.0)])
        activity = [index % 50 < 25 for index in range(len(levels.db))]
        plan = build_plan(levels, VAD_BASE, activity=activity)
        self.assertEqual(len(plan.keep), 2)

    def test_hangover_extends_speech_tail(self):
        levels = levels_from_frames([
            frame(HISS, 1.0), frame(SPEECH, 0.5), frame(HISS, 1.0),
        ])
        without = build_plan(levels, VAD_BASE.with_(hangover=0.0))
        with_tail = build_plan(levels, VAD_BASE.with_(hangover=0.3))
        self.assertAlmostEqual(
            with_tail.keep[0].end - without.keep[0].end, 0.3, places=1,
        )

    def test_invalid_snr_order_is_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(
                levels_from_frames([frame(SPEECH, 1.0)]),
                VAD_BASE.with_(snr_min_db=4.0, snr_exit_db=10.0),
            )


if __name__ == "__main__":
    unittest.main()
