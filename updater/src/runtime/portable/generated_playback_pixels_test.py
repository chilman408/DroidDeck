# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('generated_pixels', Path(__file__).with_name('generated-playback-pixels.py'))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)


class Pixels(unittest.TestCase):
    def test_black_static_and_moving(self):
        black, red, green = bytes(432), bytes([220, 20, 20]*144), bytes([20, 220, 20]*144)
        self.assertEqual(P.facts(None, black), (0, False))
        self.assertEqual(P.facts(red, red), (1, False))
        self.assertEqual(P.facts(red, green), (1, True))

    def test_region_and_bounds(self):
        data = bytes([220, 20, 20, 255] * (120*90))
        self.assertEqual(P.pixels(data), bytes([220, 20, 20]*144))
        for bad in (b'', data[:-1], data + b'x'):
            with self.assertRaises(ValueError): P.pixels(bad)
        with self.assertRaises(ValueError): P.facts(None, b'x')


if __name__ == '__main__': unittest.main(verbosity=2)
