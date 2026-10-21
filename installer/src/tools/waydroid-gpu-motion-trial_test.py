# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('motion_test', Path(__file__).with_name('waydroid-gpu-motion-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class MotionTrialTests(unittest.TestCase):
    def test_distinct_identity_preserves_fixed_offline_recovery(self):
        engine = tool.engine
        self.assertEqual(engine.BACKUP.name, 'gpu-render-motion-20261011i')
        self.assertEqual(engine.ORIGINAL, engine.BACKUP / 'original.cfg')
        self.assertEqual(engine.LOCK.name, '.gpu-render-motion-20261011i.lock')
        self.assertEqual(engine.start_trial, tool.wrapper.start_trial)
        self.assertEqual(engine.validate_extension, tool.wrapper.validate_extension)
        self.assertNotIn(b'setprop persist.', engine.INIT_CODE)
        with patch.object(engine.trial, 'stop') as stop:
            for profile in ['gpu-render-codec4-rgbx-layers-offline-property',
                            'gpu-render-codec4-rgbx-layers-offline-property-retest']:
                with self.assertRaisesRegex(RuntimeError, 'Invalid GPU rendering backup'):
                    engine.revert({'profile': profile})
            stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
