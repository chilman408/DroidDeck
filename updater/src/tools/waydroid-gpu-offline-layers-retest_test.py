# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('retest', Path(__file__).with_name('waydroid-gpu-offline-layers-retest.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class RetestTests(unittest.TestCase):
    def test_identity_and_recovery_do_not_reuse_prior_profiles(self):
        engine = tool.engine
        self.assertEqual(engine.BACKUP.name, 'gpu-render-layers-20261011h')
        self.assertEqual(engine.ORIGINAL, engine.BACKUP / 'original.cfg')
        self.assertEqual(engine.LOCK.name, '.gpu-render-layers-20261011h.lock')
        self.assertEqual(engine.start_trial, tool.wrapper.start_trial)
        self.assertEqual(engine.validate_extension, tool.wrapper.validate_extension)
        self.assertNotIn(b'setprop persist.', engine.INIT_CODE)
        with patch.object(engine.trial, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'Invalid GPU rendering backup'):
                engine.revert({'profile': 'gpu-render-codec4-rgbx-layers-offline-property'})
            stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
