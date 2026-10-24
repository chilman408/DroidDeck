# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('health_gpu_test', Path(__file__).with_name('waydroid-gpu-health-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class HealthTrialTests(unittest.TestCase):
    def test_distinct_backup_and_override_preserve_all_prior_trials(self):
        self.assertEqual(tool.engine.BACKUP.name, 'gpu-render-health-20261011u')
        self.assertEqual(tool.engine.LOCK.name, '.gpu-render-health-20261011u.lock')
        self.assertEqual(tool.boot.TEMP, '.tesla-mesa-allocator-20261011u.tmp')
        self.assertEqual(tool.engine.EXPECTED_NATIVE['ro.hardware.gralloc'], 'minigbm_gbm_mesa')
        self.assertEqual(tool.engine.EXPECTED_NATIVE['debug.stagefright.ccodec'], '4')

    def test_apply_requires_all_current_capture_and_frontend_bytes(self):
        self.assertEqual(tool.REQUIRED[tool.ENCODER], '63f8e631cc31822545ffca5160a46fb72e87d11f908fc4eb4d23e0e64db0d08e')
        with patch.object(tool.engine.trial, 'protected'), patch.object(tool.engine.trial, 'service', return_value='active'), patch.object(Path, 'read_bytes', return_value=b'foreign'), \
                patch.object(tool.engine, 'main') as apply:
            with self.assertRaisesRegex(RuntimeError, 'Verified streaming baseline'):
                tool.main(['--apply'])
            apply.assert_not_called()
        with patch.object(tool.engine.trial, 'protected'), patch.object(tool.engine.trial, 'service', return_value='active'), patch.object(Path, 'read_bytes', return_value=b'accepted'), \
                patch.object(tool.engine.trial, 'digest', side_effect=list(tool.REQUIRED.values())), \
                patch.object(tool.engine, 'main') as apply:
            tool.main(['--apply'])
            apply.assert_called_once_with(['--apply'])

    def test_apply_refuses_unarmed_independent_health_guard(self):
        with patch.object(tool.engine.trial, 'service', return_value='inactive'), patch.object(tool.engine, 'main') as apply:
            with self.assertRaisesRegex(RuntimeError, 'health guard'):
                tool.main(['--apply'])
            apply.assert_not_called()

    def test_rollback_remains_independent_of_changed_frontend_or_encoder(self):
        with patch.object(tool.engine.trial, 'protected') as protected, patch.object(Path, 'read_bytes') as read, \
                patch.object(tool.engine, 'main') as restore:
            tool.main(['--rollback'])
            protected.assert_not_called(); read.assert_not_called()
            restore.assert_called_once_with(['--rollback'])

    def test_arbitrary_actions_are_refused(self):
        for args in [[], ['--retry'], ['--apply', '/tmp/other'], ['--rollback', '--apply']]:
            with self.assertRaises(RuntimeError):
                tool.main(args)


if __name__ == '__main__':
    unittest.main()
