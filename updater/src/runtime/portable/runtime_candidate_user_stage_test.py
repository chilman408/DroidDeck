# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import inspect
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
import runtime_candidate_admission_test as fixtures

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('user_candidate_stage', HERE / 'runtime-candidate-stage.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(P, 'publish_user_candidate', None)), 'Account-bound held publication required')

    def test_no_owner_override_and_privilege_before_source_reads(self):
        self.assertNotIn('source_owner', inspect.signature(P.publish_user_candidate).parameters)
        with ExitStack() as stack, mock.patch.object(P.A, 'hold_user_candidate', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.publish_user_candidate(stack, 'fixture', '/home/fixture/source', '/root/parent')

    def test_user_alias_and_outside_home_refuse_before_filesystem_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')), \
                mock.patch.object(P.os, 'open', side_effect=AssertionError('no reads')):
            for user, source in (('root', '/home/fixture/source'), ('../x', '/home/fixture/source'),
                                  ('fixture', '/root/outside'), ('fixture', '/home/fixture')):
                with self.assertRaises(ValueError):
                    P.publish_user_candidate(stack, user, source, '/root/parent')

    def test_generated_proof_has_fixed_dispatch_and_redacted_failure(self):
        result = subprocess.run([sys.executable, '-B', str(HERE / 'runtime_candidate_user_stage_smoke.py'), '--activate', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'User candidate staging proof refused; preserve generated proof directories')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only account-bound publication fixtures required')
class AccountPublication(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(P, 'publish_user_candidate', None)), 'Account-bound held publication required')
        fixtures.HeldCandidate.setUp(self)
        for item in [self.source, *self.source.rglob('*')]:
            os.chown(item, 1000, 1000)
        self.parent = self.root / 'publication'
        self.parent.mkdir(mode=0o700)
        self.account = ('fixture', 1000, 1000, str(self.root))

    def publish(self, stack):
        return P.publish_user_candidate(stack, 'fixture', self.source, self.parent)

    def test_account_bound_root_copy_survives_later_account_and_source_changes(self):
        with mock.patch.object(P.A, '_account', return_value=self.account) as account, ExitStack() as stack:
            held = self.publish(stack)
            self.assertTrue(held['policy']['sourceAccountMatched'])
            self.assertTrue(held['policy']['rootPrivateCopyVerified'])
            self.assertFalse(held['policy']['sourceFilesInstalled'])
            self.assertGreater(account.call_count, 100)
            account.return_value = ('fixture', 1001, 1000, str(self.root))
            (self.source / 'portable/gpu-access.js').write_bytes(b'changed after completed publication')
            held['check']()
            self.assertEqual(len(held['read']('portable/gpu-access.js')), 11805)

    def test_account_drift_during_initial_admission_leaves_empty_parent(self):
        with mock.patch.object(P.A, '_account', side_effect=[self.account, ('fixture', 1001, 1000, str(self.root))]), \
                ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(os.listdir(self.parent), [])

    def test_account_drift_during_copy_preserves_partial_attempt(self):
        write = P._write_private
        with mock.patch.object(P.A, '_account', return_value=self.account) as account:
            def changed(*args):
                result = write(*args)
                account.return_value = ('fixture', 1001, 1000, str(self.root))
                return result
            with mock.patch.object(P, '_write_private', side_effect=changed), ExitStack() as stack, \
                    self.assertRaises(P.CandidatePublicationUncertain):
                self.publish(stack)
        self.assertFalse((self.parent / 'candidate').exists())
        self.assertEqual(len(os.listdir(self.parent)), 1)

    def test_account_drift_during_independent_final_verification_is_uncertain(self):
        original = P.A.hold_runtime_candidate
        with mock.patch.object(P.A, '_account', return_value=self.account) as account:
            def changed(stack, root, **kwargs):
                held = original(stack, root, **kwargs)
                if Path(root) == self.parent / 'candidate':
                    account.return_value = ('fixture', 1001, 1000, str(self.root))
                return held
            with mock.patch.object(P.A, 'hold_runtime_candidate', side_effect=changed), ExitStack() as stack, \
                    self.assertRaises(P.CandidatePublicationUncertain):
                self.publish(stack)
        self.assertTrue((self.parent / 'candidate/bundle-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
