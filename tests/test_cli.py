import unittest

from silence_cut.cli import build_parser


class CliDefaultTest(unittest.TestCase):
    def test_analyze_defaults_to_speech(self):
        args = build_parser().parse_args(["analyze", "input.mp4"])
        self.assertEqual(args.mode, "speech")

    def test_cut_defaults_to_speech(self):
        args = build_parser().parse_args(["cut", "input.mp4"])
        self.assertEqual(args.mode, "speech")


if __name__ == "__main__":
    unittest.main()
