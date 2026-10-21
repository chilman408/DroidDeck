# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('source_test', Path(__file__).with_name('waydroid-gpu-source-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class SourceTrialTests(unittest.TestCase):
    def test_diagnostic_has_separate_backup_and_offline_recovery(self):
        engine = tool.engine
        self.assertEqual(engine.BACKUP.name, 'gpu-render-source-20261011k')
        self.assertEqual(engine.ORIGINAL, engine.BACKUP / 'original.cfg')
        self.assertEqual(engine.LOCK.name, '.gpu-render-source-20261011k.lock')
        self.assertEqual(engine.start_trial, tool.wrapper.start_trial)
        self.assertEqual(engine.validate_extension, tool.wrapper.validate_extension)
        self.assertNotIn(b'setprop persist.', engine.INIT_CODE)
        with patch.object(engine.trial, 'stop') as stop:
            for profile in ['gpu-render-codec4-rgbx-layers-motion-verified-control',
                            'gpu-render-codec4-rgbx-layers-offline-property-retest']:
                with self.assertRaisesRegex(RuntimeError, 'Invalid GPU rendering backup'):
                    engine.revert({'profile': profile})
            stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
