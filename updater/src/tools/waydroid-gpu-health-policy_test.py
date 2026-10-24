# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('gpu_health_policy', Path(__file__).with_name('waydroid-gpu-health-policy.py'))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class HealthPolicyTests(unittest.TestCase):
    def test_healthy_gpu_has_no_user_validation_deadline(self):
        for age in [0, 480, 86400, 365 * 86400]:
            self.assertEqual(policy.decision('ready', True, 2, age), ('continue', 0))

    def test_only_consecutive_failures_request_recovery(self):
        failures = 0
        for expected in [1, 2]:
            action, failures = policy.decision('ready', False, failures, 1000)
            self.assertEqual((action, failures), ('continue', expected))
        self.assertEqual(policy.decision('ready', True, failures, 1001), ('continue', 0))
        self.assertEqual(policy.decision('ready', False, 2, 1002), ('recover', 3))

    def test_engineering_startup_limit_is_not_a_ready_gpu_expiry(self):
        for stage in policy.STARTING:
            self.assertEqual(policy.decision(stage, None, 0, 479), ('continue', 0))
            self.assertEqual(policy.decision(stage, None, 0, 480), ('recover', 0))
        self.assertEqual(policy.decision('recovery-required', None, 0, 0), ('recover', 0))
        self.assertEqual(policy.decision('restored', None, 0, 100000), ('stop', 0))

    def test_invalid_observations_never_become_healthy(self):
        for args in [('foreign', True, 0, 0), ('ready', 'true', 0, 0), ('ready', None, 0, 0),
                     ('ready', True, -1, 0), ('ready', True, 4, 0), ('ready', True, 0, float('nan')),
                     ('ready', True, 0, -1)]:
            with self.assertRaises(ValueError):
                policy.decision(*args)


if __name__ == '__main__':
    unittest.main()
