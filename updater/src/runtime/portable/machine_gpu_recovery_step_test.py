# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_step_fixtures', Path(__file__).with_name('machine_gpu_recovery_status_test.py'))
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
R, P = S.R, S.P


class StepBoundary(unittest.TestCase):
    def test_no_unsupervised_unsequenced_or_cli_execution(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_compound', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'expected_sequence': 0}, {'expected_sequence': True, 'supervised': lambda: None},
                           {'expected_sequence': -1, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_recovery_step(mock.Mock(), **kwargs)
            for option in ('--machine-gpu-recovery-step', '--recover-machine-gpu'):
                with self.assertRaises(ValueError):
                    P.main([option])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated recovery-step fixtures required')
class RecoveryStep(unittest.TestCase):
    def setUp(self):
        self.fixture = S.RecoveryStatus()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.journal = self.fixture.journal
        self.base = self.journal.base

    def step(self, sequence=0, supervised=lambda: None):
        return P._machine_gpu_recovery_step(self.journal.copier, self.journal.parent, self.base.cfg,
            self.base.fixture.metadata_root, self.base.backup, expected_sequence=sequence,
            supervised=supervised, **self.journal.fixture.kwargs())

    def test_five_real_file_actions_restore_exact_original_inodes_and_require_each_checkpoint(self):
        cfg_original = (self.base.cfg.parent / 'pending-droiddeck-machine-gpu.cfg').stat().st_ino
        property_original = (self.base.saved.parent / 'pending-droiddeck-layer-machine-on').stat().st_ino
        expected = (('allocator', 'retain-candidate'), ('savedLayer', 'restore-original'), ('savedLayer', 'retain-candidate'),
                    ('configuration', 'restore-original'), ('configuration', 'retain-candidate'))
        for sequence, (role, operation) in enumerate(expected):
            original_record = self.journal.record.read_bytes()
            result = self.step(sequence)
            self.assertEqual(result['actionPerformed'], {'component': role, 'operation': operation})
            self.assertEqual(result['sequence'], sequence)
            self.assertTrue(result['checkpointRequired'])
            self.assertIsNone(result['nextAction'])
            self.assertEqual(self.journal.record.read_bytes(), original_record)
            status = self.fixture.status()
            self.assertTrue(status['checkpointRequired'])
            with self.assertRaises(ValueError):
                self.step(sequence)
            self.journal.checkpoint(expected_sequence=sequence)
        self.assertEqual(self.base.cfg.stat().st_ino, cfg_original)
        self.assertEqual(self.base.saved.stat().st_ino, property_original)
        self.assertEqual(self.base.cfg.read_bytes(), self.base.baseline)
        self.assertEqual(self.base.saved.read_bytes(), self.base.original)
        before = self.fixture.snapshot()
        terminal = self.step(5)
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertIsNone(terminal['actionPerformed'])
        self.assertFalse(terminal['checkpointRequired'])
        for flag in ('recoveryJournalChanged', 'configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged',
                     'servicesActivated', 'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed',
                     'installable', 'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified'):
            self.assertFalse(terminal[flag])
        self.assertNotIn(str(self.base.root), json.dumps(terminal))
        self.assertNotIn('savedDocumentSha256', terminal)

    def test_stale_sequence_partial_journal_and_foreign_candidate_preserve_files(self):
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.step(1)
        self.assertEqual(self.fixture.snapshot(), before)
        partial = self.journal.final / 'pending-recovery.json'
        partial.write_bytes(b'generated partial')
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.step()
        self.assertEqual(self.fixture.snapshot(), before)
        partial.unlink()
        current = self.base.misc / 'waydroid_settings'
        alternate = self.base.misc / 'foreign'
        alternate.write_bytes(current.read_bytes())
        alternate.chmod(0o600)
        alternate.replace(current)
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.step()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_supervisor_failure_before_action_preserves_all_files(self):
        before = self.fixture.snapshot()
        calls = 0
        def deny():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.step(supervised=deny)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_failed_atomic_rename_is_not_reported_as_progress(self):
        before = self.fixture.snapshot()
        with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated rename refused')), self.assertRaises(OSError):
            self.step()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_post_action_supervisor_timeout_is_uncertain_and_status_recovers_observation(self):
        def timeout():
            if (self.base.misc / 'pending-droiddeck-allocator-machine-restored').exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.step(supervised=timeout)
        self.assertTrue(self.fixture.status()['checkpointRequired'])
        self.assertEqual(json.loads(self.journal.record.read_bytes())['sequence'], 0)
        with self.assertRaises(ValueError):
            self.step()
        self.journal.checkpoint(expected_sequence=0)
        self.assertEqual(self.step(1)['actionPerformed']['component'], 'savedLayer')

    def test_post_action_fsync_failure_retains_candidate_and_reports_uncertainty(self):
        with mock.patch.object(P.os, 'fsync', side_effect=OSError('generated durability failure')), self.assertRaises(P.PromotionUncertain):
            self.step()
        self.assertTrue((self.base.misc / 'pending-droiddeck-allocator-machine-restored').exists())
        self.assertTrue(self.fixture.status()['checkpointRequired'])

    def test_continuous_lock_excludes_other_admission_through_refresh_and_return(self):
        import fcntl
        calls = 0
        def supervised():
            nonlocal calls
            if not (self.base.misc / 'pending-droiddeck-allocator-machine-restored').exists():
                return
            fd = os.open(self.journal.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                calls += 1
            finally:
                os.close(fd)
        self.step(supervised=supervised)
        self.assertGreaterEqual(calls, 2)
        with ExitStack() as stack:
            self.journal.fixture.hold(stack)  # Original borrowed descriptor released only at function exit.

    def test_borrowed_descriptor_must_be_exact_root_transaction_directory(self):
        for target in (True, os.open(self.journal.parent, os.O_RDONLY | os.O_DIRECTORY)):
            try:
                with ExitStack() as stack, self.assertRaises(ValueError):
                    P._hold_machine_gpu_compound(self.journal.copier, stack, self.journal.parent, self.base.cfg,
                        self.base.fixture.metadata_root, self.base.backup, _transaction_fd=target,
                        **self.journal.fixture.kwargs())
            finally:
                if type(target) is int:
                    os.close(target)

    def test_journal_replacement_after_action_is_uncertain_not_silent_success(self):
        altered = False
        def supervised():
            nonlocal altered
            if not altered and (self.base.misc / 'pending-droiddeck-allocator-machine-restored').exists():
                alternate = self.journal.final / 'replacement'
                alternate.write_bytes(self.journal.record.read_bytes())
                alternate.chmod(0o600)
                alternate.replace(self.journal.record)
                altered = True
        with self.assertRaises(P.PromotionUncertain):
            self.step(supervised=supervised)
        self.assertTrue(altered)

    def test_exchange_post_action_timeout_retains_original_and_requires_checkpoint(self):
        self.step()
        self.journal.checkpoint(expected_sequence=0)
        original_inode = (self.base.saved.parent / 'pending-droiddeck-layer-machine-on').stat().st_ino
        def timeout():
            if self.base.saved.stat().st_ino == original_inode:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.step(1, supervised=timeout)
        self.assertEqual(self.base.saved.stat().st_ino, original_inode)
        self.assertTrue(self.fixture.status()['checkpointRequired'])

    def test_post_action_candidate_and_receipt_replacement_is_uncertain(self):
        altered = False
        def supervised():
            nonlocal altered
            retained = self.base.misc / 'pending-droiddeck-allocator-machine-restored'
            if not altered and retained.exists():
                alternate = self.base.misc / 'replacement'
                alternate.write_bytes(retained.read_bytes())
                alternate.chmod(0o600)
                alternate.replace(retained)
                receipt = self.journal.fixture.state / 'mesa-allocator-machine/receipt.json'
                value = json.loads(receipt.read_bytes())
                value['fileDevice'], value['fileInode'] = retained.stat().st_dev, retained.stat().st_ino
                receipt.write_bytes((json.dumps(value, indent=2) + '\n').encode())
                altered = True
        with self.assertRaises(P.PromotionUncertain):
            self.step(supervised=supervised)
        self.assertTrue(altered)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated recovery matrix required')
class RecoveryMatrix(unittest.TestCase):
    def test_all_64_generated_compound_states_execute_only_forward_owned_steps(self):
        for values in itertools.product(('baseline', 'staged', 'selected', 'restored'), repeat=3):
            with self.subTest(states=values):
                fixture = R.C.CompoundOwnership()
                fixture.setUp()
                try:
                    base = fixture.fixture
                    for role, value in zip(R.ROLES, values):
                        fixture.select(role, value)
                    args = (fixture.copier, base.parent, base.cfg, base.fixture.metadata_root, base.backup)
                    kwargs = fixture.kwargs()
                    saved = P._machine_gpu_recovery_checkpoint(*args, initialize=True, supervised=lambda: None, **kwargs)
                    for _ in range(6):
                        result = P._machine_gpu_recovery_step(*args, expected_sequence=saved['sequence'], supervised=lambda: None, **kwargs)
                        if result['actionPerformed'] is None:
                            break
                        self.assertTrue(P._machine_recovery_step(saved['states'], result['observedStates']))
                        saved = P._machine_gpu_recovery_checkpoint(*args, expected_sequence=saved['sequence'], supervised=lambda: None, **kwargs)
                    self.assertIsNone(result['actionPerformed'])
                    self.assertEqual(base.cfg.read_bytes(), base.baseline)
                    self.assertEqual(base.saved.read_bytes(), base.original)
                    self.assertFalse((base.misc / 'waydroid_settings').exists())
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
