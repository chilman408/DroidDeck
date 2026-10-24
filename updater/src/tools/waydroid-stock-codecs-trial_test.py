# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('stock_trial', Path(__file__).with_name('waydroid-stock-codecs-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
SOURCE = b'<Included><Decoders><MediaCodec name="c2.android.avc.decoder" type="video/avc"/><MediaCodec name="c2.android.hevc.decoder" type="video/hevc"/></Decoders></Included>'


class StockCodecTests(unittest.TestCase):
    def test_candidate_only_connects_existing_video_declarations(self):
        tool.validate_source(SOURCE)
        root = ET.fromstring(tool.CODE)
        self.assertEqual(root.tag, 'Included')
        self.assertEqual([item.attrib for item in root], [{'href': 'media_codecs_google_c2_video.xml'}])
        self.assertNotIn(b'secure-playback', tool.CODE)
        self.assertNotIn(b'audio', tool.CODE)

    def test_source_rejects_missing_duplicate_wrong_type_and_entities(self):
        for source in [b'<Included/>', SOURCE.replace(b'video/avc', b'audio/ac3'),
                       SOURCE.replace(b'</Decoders>', b'<MediaCodec name="c2.android.avc.decoder" type="video/avc"/></Decoders>'),
                       b'<!DOCTYPE Included><Included/>', b'<!ENTITY foo "bar"><Included/>', b'x' * 65537]:
            with self.assertRaises(ValueError):
                tool.validate_source(source)

    def test_rollback_refuses_foreign_overlay_without_stopping_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, overlay, staging = root / 'config', root / 'overlay', root / 'staging'
            config.write_bytes(b'config')
            overlay.write_bytes(b'foreign')
            manifest = {'profile': 'stock-video-codecs', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config')}
            with patch.object(tool.trial, 'CFG', config), patch.object(tool, 'OVERLAY', overlay), \
                    patch.object(tool, 'TEMP', staging), patch.object(tool.trial, 'protected'), \
                    patch.object(tool.trial, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    tool.revert(manifest)
                stop.assert_not_called()
            self.assertEqual(overlay.read_bytes(), b'foreign')

    def test_rollback_only_removes_owned_overlay_and_recovers_h264(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, overlay, staging = root / 'config', root / 'overlay', root / 'staging'
            config.write_bytes(b'config')
            overlay.write_bytes(tool.CODE)
            manifest = {'profile': 'stock-video-codecs', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config')}
            calls = []
            with patch.object(tool.trial, 'CFG', config), patch.object(tool, 'OVERLAY', overlay), \
                    patch.object(tool, 'TEMP', staging), patch.object(tool.trial, 'protected'), \
                    patch.object(tool.trial, 'stop', lambda: calls.append('stop')), \
                    patch.object(tool.trial, 'start', lambda: calls.append('start')), \
                    patch.object(tool.trial, 'ready', lambda: calls.append('ready')):
                tool.revert(manifest)
            self.assertFalse(overlay.exists())
            self.assertEqual(config.read_bytes(), b'config')
            self.assertEqual(calls, ['stop', 'start', 'ready'])


if __name__ == '__main__':
    unittest.main()
