# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('producer', Path(__file__).with_name('compositor-h264.py'))
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)

PLAN = dict(serial=99, width=980, height=720, sourceWidth=1280, sourceHeight=720,
            left=150, right=150, top=0, bottom=0, bitRate=4000000, maxFps=30)


class ProtocolTests(unittest.TestCase):
    def test_portable_worker_identity_requires_exact_unprivileged_uid_and_lease(self):
        owner = 'a' * 32
        producer.validate_worker_identity(1000, ['{}'], owner)
        producer.validate_worker_identity(1200, ['{}', '--uid', '1200'], owner)
        for uid, args, lease in [(0, ['{}'], owner), (1200, ['{}'], owner),
                                 (1000, ['{}', '--uid', '1200'], owner),
                                 (1200, ['{}', '--uid', '01200'], owner),
                                 (1200, ['{}', '--uid', '1200;sh'], owner),
                                 (1200, ['{}', '--uid', '1200', 'extra'], owner),
                                 (999, ['{}', '--uid', '999'], owner),
                                 (1200, ['{}', '--uid', '1200'], 'forged')]:
            with self.assertRaises(RuntimeError):
                producer.validate_worker_identity(uid, args, lease)

    def test_config_and_key_headers_match_existing_relay(self):
        flags, size = struct.unpack('>QI', producer.packet(b'abc', 456, key=True)[:12])
        self.assertEqual(flags, (1 << 61) | 456)
        self.assertEqual(size, 3)
        self.assertEqual(producer.packet(b'x', config=True)[0], 0x40)
        for payload, pts in [(b'', 0), (b'x' * (producer.MAX_PACKET + 1), 0), (b'x', -1), (b'x', 1 << 61)]:
            with self.assertRaises(ValueError):
                producer.packet(payload, pts)

    def test_only_sps_and_pps_enter_configuration(self):
        payload = b'\0\0\1\x09\xf0\0\0\0\1\x67abc\0\0\1\x68def\0\0\0\1\x65picture'
        self.assertEqual(producer.parameter_sets(payload), b'\0\0\0\1\x67abc\0\0\0\1\x68def')
        for invalid in [b'', b'\0\0\1\x65picture', b'\0\0\1\x67abc', b'\0\0\1\0\0\1\x68abc']:
            with self.assertRaises(ValueError):
                producer.parameter_sets(invalid)

    def test_validated_crop_and_profile_build_fixed_pipeline(self):
        pipeline = producer.pipeline_description(PLAN)
        self.assertIn('target-object=99', pipeline)
        self.assertIn('left=150 top=0 right=150 bottom=0', pipeline)
        self.assertIn('width=980,height=720,framerate=[0/1,30/1]', pipeline)
        self.assertIn('profile=baseline,stream-format=byte-stream,alignment=au', pipeline)
        for key, value in [('serial', 0), ('width', 981), ('height', 2000), ('left', 1280),
                           ('maxFps', 0), ('bitRate', 123), ('serial', '99 ! fakesink'), ('top', True)]:
            with self.assertRaises(ValueError):
                producer.pipeline_description(dict(PLAN, **{key: value}))

    def test_overload_discards_raw_frames_not_encoded_references(self):
        pipeline = producer.pipeline_description(PLAN)
        self.assertIn('appsink name=frames sync=false max-buffers=2 drop=false', pipeline)
        raw_queue = 'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream'
        self.assertIn(raw_queue, pipeline)
        self.assertLess(pipeline.index(raw_queue), pipeline.index('x264enc'))
        self.assertGreater(pipeline.index('appsink'), pipeline.index('x264enc'))

    def test_three_mbps_uses_kilobits_in_x264_without_changing_geometry_or_frame_rate(self):
        pipeline = producer.pipeline_description(dict(PLAN, bitRate=3000000))
        self.assertIn('bitrate=3000 ', pipeline)
        self.assertIn('width=980,height=720,framerate=[0/1,30/1]', pipeline)
        for invalid in (3000001, '3000000', 3000000.5):
            with self.assertRaises(ValueError):
                producer.pipeline_description(dict(PLAN, bitRate=invalid))

    def test_idle_source_does_not_generate_catchup_duplicates(self):
        self.assertIn('videorate drop-only=true max-rate=30 !', producer.pipeline_description(PLAN))
        for rate in (30, 45, 60):
            pipeline = producer.pipeline_description(dict(PLAN, maxFps=rate))
            self.assertIn(f'max-rate={rate}', pipeline)
            self.assertIn(f'framerate=[0/1,{rate}/1]', pipeline)


if __name__ == '__main__':
    unittest.main()
