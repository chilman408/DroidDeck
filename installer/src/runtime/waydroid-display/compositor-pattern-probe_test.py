# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('pattern', Path(__file__).with_name('compositor-pattern-probe.py'))
pattern = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pattern)


class PatternTests(unittest.TestCase):
    @staticmethod
    def cpu_frame(phase):
        row = bytes((*pattern.PALETTE[phase], 255)) * 160 + bytes((*pattern.PALETTE[(phase + 1) % 4], 255)) * 160
        return row * 240

    def test_cpu_phases_validate_both_halves_and_channel_order(self):
        for phase in range(4):
            data = self.cpu_frame(phase)
            self.assertEqual(pattern.cpu_phase(data), phase)
        swapped = bytearray(self.cpu_frame(0))
        for i in range(0, len(swapped), 4):
            swapped[i], swapped[i + 2] = swapped[i + 2], swapped[i]
        with self.assertRaisesRegex(RuntimeError, 'colors or channel'):
            pattern.cpu_phase(swapped)
        with self.assertRaisesRegex(RuntimeError, 'geometry'):
            pattern.cpu_phase(b'')

    def test_hash_noise_cannot_count_as_cpu_motion(self):
        first = self.cpu_frame(0)
        noise = bytes(min(255, value + 1) for value in first)
        self.assertFalse(pattern.meaningful_motion(first, noise))
        self.assertFalse(pattern.meaningful_motion(first, first))
        self.assertFalse(pattern.meaningful_motion(None, first))
        self.assertTrue(pattern.meaningful_motion(first, self.cpu_frame(1)))
        with self.assertRaisesRegex(RuntimeError, 'motion buffers'):
            pattern.meaningful_motion(first, b'')

    def test_blank_is_not_visible_dma_buffer_evidence(self):
        self.assertIsNone(pattern.frame_summary(bytes(320 * 240 * 4), 320, 240, True))

    def test_dma_buffer_checks_pixels_not_only_timestamps(self):
        a = bytearray([80, 90, 100, 255] * (320 * 240))
        first = pattern.frame_summary(a, 320, 240, True)
        self.assertEqual(first['nonblackFraction'], 1)
        self.assertEqual(first, pattern.frame_summary(a, 320, 240, True))
        a[1200] = 255
        self.assertNotEqual(first['pixelDigest'], pattern.frame_summary(a, 320, 240, True)['pixelDigest'])

    def test_cairo_retains_strict_known_color_checks(self):
        data = bytearray([80, 90, 100, 255] * (320 * 240))
        with self.assertRaisesRegex(RuntimeError, 'color channels'):
            pattern.frame_summary(data, 320, 240)
        for x, color in [(160, [0, 255, 0]), (265, [0, 0, 255])]:
            offset = (30 * 320 + x) * 4
            data[offset:offset + 3] = bytes(color)
        self.assertEqual(pattern.frame_summary(data, 320, 240)['nonblackFraction'], 1)

    def test_geometry_and_stride_are_fixed(self):
        for data, width, height in [(b'', 320, 240), (bytes(320 * 240 * 4), 240, 320)]:
            with self.assertRaisesRegex(RuntimeError, 'geometry or stride'):
                pattern.frame_summary(data, width, height, True)


if __name__ == '__main__':
    unittest.main()
