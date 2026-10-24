# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('source_motion', Path(__file__).parents[1] / 'runtime/preview-gateway/generated-compositor-motion.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class SourceMotionTests(unittest.TestCase):
    def test_raw_probe_never_encodes_streams_or_saves_pixels(self):
        plan = dict(serial=123, width=960, height=720, sourceWidth=1280, sourceHeight=720,
                    left=160, right=160, top=0, bottom=0, bitRate=4000000, maxFps=30)
        pipeline = tool.pipeline_description(plan)
        self.assertIn('target-object=123', pipeline)
        self.assertIn('videocrop left=160 top=0 right=160 bottom=0', pipeline)
        self.assertIn('format=RGBA,width=120,height=90', pipeline)
        for forbidden in ['x264', 'filesink', 'tcp', 'socket', 'png', 'fakesink']:
            self.assertNotIn(forbidden, pipeline)
        with self.assertRaises(ValueError):
            tool.pipeline_description({**plan, 'serial': '123 ! filesink'})

    def test_region_has_fixed_bounds(self):
        self.assertEqual(len(tool.video_region(bytes([200, 10, 10, 255]) * 120 * 90)), 432)
        with self.assertRaises(ValueError):
            tool.video_region(b'x')

    def test_codec_noise_does_not_count_as_source_motion(self):
        before, after = bytes([200, 10, 10]) * 144, bytes([202, 12, 12]) * 144
        facts = tool.pixel_facts(before, after)
        self.assertFalse(facts['moving'])
        self.assertEqual(facts['changedRatio'], 0)
        self.assertEqual(facts['meanDifference'], 2)

    def test_large_changes_count_as_motion_but_first_frame_does_not(self):
        before, after = bytes([200, 10, 10]) * 144, bytes([10, 200, 10]) * 144
        self.assertFalse(tool.pixel_facts(None, before)['moving'])
        self.assertTrue(tool.pixel_facts(before, after)['moving'])
        self.assertEqual(tool.pixel_facts(before, after)['changedRatio'], 1)
        self.assertEqual(tool.pixel_facts(before, after)['colorfulRatio'], 1)

    def test_invalid_geometry_and_sparse_changes_do_not_pass(self):
        before = bytes([200, 10, 10]) * 144
        after = bytes([10, 200, 10]) + before[3:]
        self.assertFalse(tool.pixel_facts(before, after)['moving'])
        with self.assertRaises(ValueError):
            tool.pixel_facts(before, b'x')


if __name__ == '__main__':
    unittest.main()
