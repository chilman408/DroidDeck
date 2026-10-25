# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cadence_gpu_test', Path(__file__).with_name('waydroid-gpu-cadence-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class CadenceTests(unittest.TestCase):
    def test_new_profile_never_reuses_o2_recovery_or_override_temporary(self):
        self.assertEqual(tool.engine.BACKUP.name, 'gpu-render-cadence-20261011r')
        self.assertEqual(tool.engine.ORIGINAL.parent, tool.engine.BACKUP)
        self.assertEqual(tool.engine.LOCK.name, '.gpu-render-cadence-20261011r.lock')
        self.assertEqual(tool.boot.TEMP, '.tesla-mesa-allocator-20261011r.tmp')
        self.assertEqual(tool.boot.CODE, b'ro.hardware.gralloc=minigbm_gbm_mesa\n')
        self.assertEqual(tool.engine.EXPECTED_NATIVE['ro.hardware.gralloc'], 'minigbm_gbm_mesa')
        self.assertEqual(tool.engine.RESTORED_PROPERTIES['ro.hardware.gralloc'], 'default')

    def test_apply_refuses_different_encoder_before_any_trial_mutation(self):
        with patch.object(tool.engine.trial, 'protected') as protected, \
                patch.object(Path, 'read_bytes', return_value=b'foreign'), \
                patch.object(tool.engine, 'main') as apply:
            with self.assertRaisesRegex(RuntimeError, 'capture fix'):
                tool.main(['--apply'])
            protected.assert_called_once_with(tool.ENCODER)
            apply.assert_not_called()

    def test_pinned_encoder_allows_apply_but_no_unbounded_arguments(self):
        with patch.object(tool.engine.trial, 'protected'), patch.object(Path, 'read_bytes', return_value=b'accepted'), \
                patch.object(tool.engine.trial, 'digest', return_value=tool.ENCODER_SHA256), \
                patch.object(tool.engine, 'main') as apply:
            tool.main(['--apply'])
            apply.assert_called_once_with(['--apply'])
        for args in [[], ['--apply', '/tmp/other'], ['--retry'], ['--rollback', '--apply']]:
            with self.assertRaises(RuntimeError):
                tool.main(args)

    def test_rollback_does_not_depend_on_encoder_or_modify_it(self):
        with patch.object(tool.engine.trial, 'protected') as protected, \
                patch.object(Path, 'read_bytes') as read, patch.object(tool.engine, 'main') as restore:
            tool.main(['--rollback'])
            protected.assert_not_called()
            read.assert_not_called()
            restore.assert_called_once_with(['--rollback'])


if __name__ == '__main__':
    unittest.main()
