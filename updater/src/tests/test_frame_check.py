import unittest

from probe.frame_check import classify_frame, screen_media


class FrameCheckTests(unittest.TestCase):
    def test_near_black_frame_is_black(self):
        self.assertEqual("black", classify_frame(bytes([12, 12, 12] * 20), 20, 1))

    def test_five_percent_bright_varied_pixels_are_visible(self):
        pixels = bytes([0, 0, 0] * 19 + [255, 255, 255])

        self.assertEqual("visible", classify_frame(pixels, 20, 1))

    def test_wrong_length_or_dimensions_are_invalid(self):
        self.assertEqual("invalid", classify_frame(b"\x00" * 5, 2, 1))
        self.assertEqual("invalid", classify_frame(b"", 0, 1))

    def test_uniform_bright_frame_does_not_prove_video(self):
        self.assertEqual("invalid", classify_frame(bytes([255, 255, 255] * 20), 20, 1))

    def test_audio_with_black_capture_fails(self):
        self.assertEqual("FAIL", screen_media(True, ["black"]))

    def test_visible_capture_only_requests_human_review(self):
        self.assertEqual("REVIEW", screen_media(True, ["visible", "visible"]))
        self.assertEqual("FAIL", screen_media(False, ["visible"]))


if __name__ == "__main__":
    unittest.main()
