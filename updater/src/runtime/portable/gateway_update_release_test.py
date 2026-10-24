# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_update_tested', Path(__file__).with_name('gateway-update-release.py'))
D = importlib.util.module_from_spec(spec)
if sys.platform == 'linux': spec.loader.exec_module(D)
else:
    with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}): spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_release_wrapper_retains_checked_engine_with_independent_fixed_namespace(self):
        self.assertEqual(D.B.SELF, 'runtime/portable/gateway-update-release.py')
        self.assertEqual(D.B.CONTROLLER.as_posix(), '/var/lib/droiddeck/gateway-update-pilot')
        self.assertIn(b'gateway-update-release.py run %i', D.B.unit_bytes())
        self.assertNotIn(b'/opt/droiddeck', D.B.unit_bytes())
        self.assertEqual(D.P.sha(bytes.fromhex(D.PUBLIC_KEY)), D.KEY_PIN)
        self.assertEqual(D.SELF, 'runtime/portable/gateway-update-release.py')


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private release bootstrap fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        fixture_spec = importlib.util.spec_from_file_location('release_fixture', Path(__file__).with_name('gateway_update_test.py'))
        f = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(f); f.D = D.B
        self.f = f.Native('test_real_atomic_gateway_apply_and_explicit_rollback_restore_original_inode')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.enrollment = self.f.root / 'enrollment'
        self.policy = self.f.root / 'maintenance.conf'
        self.policy.write_bytes(b'generated fixture maintenance policy\n'); self.policy.chmod(0o644)
        self.events = []
        self.proof = {'kind': 'installed-update-base-inspection', 'originalInstallationOwnershipVerified': True,
            'configurationSha256': 'e' * 64, 'receiptGenerations': {'original': [1,2,3]}}
        for item in (patch.object(D, 'ENROLLMENT', self.enrollment), patch.object(D.B, 'POLICY', self.policy),
                     patch.object(D.T, 'machine_pin', lambda: 'a' * 64),
                     patch.object(D.B.A, 'base_proof', lambda *_: self.events.append('proof') or self.proof),
                     patch.object(D, 'reviewed_policy', lambda: b'generated fixture maintenance policy\n'),
                     patch.object(D, 'public_trust', lambda **kwargs: self.events.append('trust') or {'keySha256': D.KEY_PIN})):
            item.start(); self.addCleanup(item.stop)

    def test_existing_policy_needs_explicit_enrollment_and_preserves_same_inode(self):
        with self.assertRaises(ValueError): D.enroll_policy('completed', '/root/generated-original', accept_existing=False)
        self.assertFalse(self.enrollment.exists())
        inode = self.policy.stat().st_ino
        value = D.enroll_policy('completed', '/root/generated-original', accept_existing=True)
        self.assertEqual(value['policyGeneration'], D.E.generation(self.policy.stat()))
        self.assertEqual(self.policy.stat().st_ino, inode)
        self.assertEqual(self.events.count('proof'), 2)
        authority = D.policy_authority()
        self.assertEqual(authority['enrollmentSha256'], D.P.sha(D.P.encode(value)))
        with self.assertRaises(FileExistsError): D.enroll_policy('completed', '/root/generated-original', accept_existing=True)
        self.assertEqual(self.policy.stat().st_ino, inode)

    def test_absent_policy_is_created_by_original_enroller_not_adopted_later(self):
        self.policy.unlink()
        value = D.enroll_policy('completed', '/root/generated-original', accept_existing=False)
        self.assertIsNone(value['policyGeneration'])
        self.assertIsNone(D.policy_authority())
        self.assertFalse(self.policy.exists())

    def test_replaced_equal_byte_policy_cannot_inherit_enrollment(self):
        D.enroll_policy('completed', '/root/generated-original', accept_existing=True)
        raw = self.policy.read_bytes(); self.policy.rename(self.policy.with_name('retained-original'))
        self.policy.write_bytes(raw); self.policy.chmod(0o644)
        with self.assertRaises(ValueError): D.policy_authority()

    def test_unfinished_enrollment_is_not_adopted_or_completed(self):
        D.enroll_policy('recovered', '/root/generated-original', accept_existing=True)
        (self.enrollment / 'complete.json').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)): D.policy_authority()
        with self.assertRaises(FileExistsError): D.enroll_policy('recovered', '/root/generated-original', accept_existing=True)
        self.assertFalse((self.enrollment / 'complete.json').exists())

    def test_changed_original_base_leaves_incomplete_intent_and_cannot_start_services(self):
        count = 0
        def changed(*_):
            nonlocal count
            count += 1
            return {**self.proof, 'configurationSha256': str(count) * 64}
        with patch.object(D.B.A, 'base_proof', changed):
            with self.assertRaises(ValueError): D.enroll_policy('completed', '/root/generated-original', accept_existing=True)
        self.assertTrue((self.enrollment / 'intent.json').exists())
        self.assertFalse((self.enrollment / 'complete.json').exists())


if __name__ == '__main__': unittest.main()
