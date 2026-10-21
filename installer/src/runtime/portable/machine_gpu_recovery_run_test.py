# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_run_fixtures', Path(__file__).with_name('machine_gpu_recovery_step_test.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
P, R = T.P, T.R


class RunBoundary(unittest.TestCase):
    def test_no_cli_unsupervised_or_unsequenced_recovery_pass(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_compound', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'expected_sequence': 0}, {'expected_sequence': True, 'supervised': lambda: None},
                           {'expected_sequence': -1, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_recovery_run(mock.Mock(), **kwargs)
            with self.assertRaises(ValueError):
                P.main(['--machine-gpu-recovery-run'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated recovery controller fixtures required')
class RecoveryRun(unittest.TestCase):
    def setUp(self):
        self.fixture = T.RecoveryStep()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.journal, self.base = self.fixture.journal, self.fixture.base
        self.status_fixture = self.fixture.fixture

    def run_recovery(self, sequence=0, supervised=lambda: None):
        return P._machine_gpu_recovery_run(self.journal.copier, self.journal.parent, self.base.cfg,
            self.base.fixture.metadata_root, self.base.backup, expected_sequence=sequence,
            supervised=supervised, **self.journal.fixture.kwargs())

    def test_full_pass_saves_every_action_and_terminal_repeat_is_read_only(self):
        cfg_inode = (self.base.cfg.parent / 'pending-droiddeck-machine-gpu.cfg').stat().st_ino
        property_inode = (self.base.saved.parent / 'pending-droiddeck-layer-machine-on').stat().st_ino
        result = self.run_recovery()
        self.assertEqual(result['actionsPerformed'], 5)
        self.assertEqual(result['progressCheckpoints'], 5)
        self.assertEqual(result['sequence'], 5)
        self.assertEqual(result['stage'], 'settled')
        self.assertTrue(result['ownedDataRestored'])
        self.assertEqual(self.base.cfg.stat().st_ino, cfg_inode)
        self.assertEqual(self.base.saved.stat().st_ino, property_inode)
        self.assertEqual(self.base.cfg.read_bytes(), self.base.baseline)
        self.assertEqual(self.base.saved.read_bytes(), self.base.original)
        self.assertIsNone(self.status_fixture.status()['nextAction'])
        self.assertTrue(all(result[key] for key in ('recoveryJournalChanged', 'configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged')))
        for flag in ('servicesActivated', 'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed',
                     'installable', 'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified'):
            self.assertFalse(result[flag])
        self.assertNotIn(str(self.base.root), json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)
        before = self.status_fixture.snapshot()
        again = self.run_recovery(5)
        self.assertEqual(self.status_fixture.snapshot(), before)
        self.assertEqual((again['actionsPerformed'], again['progressCheckpoints']), (0, 0))
        self.assertFalse(again['recoveryJournalChanged'])

    def test_interrupted_step_is_checkpointed_before_any_next_action(self):
        def timeout():
            if (self.base.misc / 'pending-droiddeck-allocator-machine-restored').exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.run_recovery(supervised=timeout)
        self.assertTrue(self.status_fixture.status()['checkpointRequired'])
        step = P._machine_gpu_recovery_step
        def verify(*args, **kwargs):
            self.assertEqual(json.loads(self.journal.record.read_bytes())['sequence'], kwargs['expected_sequence'])
            self.assertGreaterEqual(kwargs['expected_sequence'], 1)
            return step(*args, **kwargs)
        with mock.patch.object(P, '_machine_gpu_recovery_step', side_effect=verify):
            result = self.run_recovery()
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints'], result['sequence']), (4, 5, 5))

    def test_post_checkpoint_timeout_resumes_from_new_durable_sequence(self):
        checkpoint = P._machine_gpu_recovery_checkpoint
        def timeout(*args, **kwargs):
            checkpoint(*args, **kwargs)
            raise subprocess.TimeoutExpired('generated post-save interruption', 5)
        with mock.patch.object(P, '_machine_gpu_recovery_checkpoint', side_effect=timeout), self.assertRaises(P.PromotionUncertain):
            self.run_recovery()
        self.assertEqual(json.loads(self.journal.record.read_bytes())['sequence'], 1)
        with self.assertRaises(ValueError):
            self.run_recovery()
        result = self.run_recovery(1)
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints'], result['sequence']), (4, 4, 5))

    def test_partial_progress_save_is_preserved_and_not_blindly_repaired(self):
        with mock.patch.object(P.os, 'replace', side_effect=OSError('generated save failed')), self.assertRaises(P.PromotionUncertain):
            self.run_recovery()
        partial = self.journal.final / 'pending-recovery.json'
        self.assertTrue(partial.exists())
        before = self.status_fixture.snapshot()
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertEqual(self.status_fixture.snapshot(), before)

    def test_stale_sequence_and_missing_journal_do_not_change_files(self):
        before = self.status_fixture.snapshot()
        with self.assertRaises(ValueError):
            self.run_recovery(1)
        self.assertEqual(self.status_fixture.snapshot(), before)
        self.journal.record.unlink()
        self.journal.final.rmdir()
        before = self.status_fixture.snapshot()
        with self.assertRaises(FileNotFoundError):
            self.run_recovery()
        self.assertEqual(self.status_fixture.snapshot(), before)

    def test_sequence_capacity_is_checked_before_any_action(self):
        value = json.loads(self.journal.record.read_bytes())
        value['sequence'] = 2**63 - 4
        self.journal.record.write_bytes((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
        before = self.status_fixture.snapshot()
        with self.assertRaises(ValueError):
            self.run_recovery(value['sequence'])
        self.assertEqual(self.status_fixture.snapshot(), before)

    def test_out_of_order_initial_observation_does_not_change_files(self):
        self.journal.exchange_original('configuration')
        before = self.status_fixture.snapshot()
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertEqual(self.status_fixture.snapshot(), before)

    def test_one_continuous_lock_covers_actions_checkpoints_and_whole_return(self):
        import fcntl
        calls = 0
        def locked():
            nonlocal calls
            fd = os.open(self.journal.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                calls += 1
            finally:
                os.close(fd)
        step, checkpoint = P._machine_gpu_recovery_step, P._machine_gpu_recovery_checkpoint
        def operation(fn):
            def check(*args, **kwargs):
                locked()
                result = fn(*args, **kwargs)
                locked()
                return result
            return check
        with mock.patch.object(P, '_machine_gpu_recovery_step', side_effect=operation(step)), \
                mock.patch.object(P, '_machine_gpu_recovery_checkpoint', side_effect=operation(checkpoint)):
            self.run_recovery()
        self.assertEqual(calls, 20)
        fd = os.open(self.journal.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_supervisor_refusal_before_action_does_not_change_files(self):
        before = self.status_fixture.snapshot()
        calls = 0
        def deny():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise subprocess.TimeoutExpired('generated supervisor unavailable', 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_recovery(supervised=deny)
        self.assertEqual(self.status_fixture.snapshot(), before)

    def test_terminal_supervisor_data_change_is_not_reported_as_settled(self):
        self.run_recovery()
        calls = 0
        def change():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.base.cfg.write_bytes(b'generated changed configuration')
        with self.assertRaises(ValueError):
            self.run_recovery(5, supervised=change)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated recovery pass matrix required')
class RecoveryRunMatrix(unittest.TestCase):
    def test_all_64_generated_states_settle_in_at_most_five_actions(self):
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
                    P._machine_gpu_recovery_checkpoint(*args, initialize=True, supervised=lambda: None, **kwargs)
                    result = P._machine_gpu_recovery_run(*args, expected_sequence=0, supervised=lambda: None, **kwargs)
                    needed = sum(0 if state in ('baseline', 'restored') else 2 if role != 'allocator' and state == 'selected'
                                 else 1 for role, state in zip(R.ROLES, values))
                    self.assertEqual((result['actionsPerformed'], result['progressCheckpoints'], result['sequence']), (needed,) * 3)
                    self.assertLessEqual(needed, 5)
                    self.assertTrue(result['ownedDataRestored'])
                    self.assertEqual(base.cfg.read_bytes(), base.baseline)
                    self.assertEqual(base.saved.read_bytes(), base.original)
                    self.assertFalse((base.misc / 'waydroid_settings').exists())
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
