# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_finish_fixtures', Path(__file__).with_name('machine_gpu_recovery_status_test.py'))
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
P = S.P


class FinishBoundary(unittest.TestCase):
    def test_no_unsupervised_unsequenced_or_cli_completion(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_compound', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'expected_sequence': 0}, {'expected_sequence': True, 'supervised': lambda: None},
                           {'expected_sequence': -1, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_recovery_finish_checkpoint(mock.Mock(), **kwargs)
            with self.assertRaises(ValueError):
                P.main(['--finish-machine-gpu-recovery-checkpoint'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated staged metadata fixtures required')
class FinishCheckpoint(unittest.TestCase):
    def setUp(self):
        self.fixture = S.RecoveryStatus()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.journal, self.base = self.fixture.journal, self.fixture.base
        self.journal.retain_allocator()  # TEST-only generated action preceding interrupted progress save.
        with mock.patch.object(P.os, 'replace', side_effect=OSError('generated save interrupted')), self.assertRaises(OSError):
            self.journal.checkpoint(expected_sequence=0)
        self.pending = self.journal.final / 'pending-recovery.json'

    def complete(self, sequence=0, supervised=lambda: None):
        return P._machine_gpu_recovery_finish_checkpoint(self.journal.copier, self.journal.parent, self.base.cfg,
            self.base.fixture.metadata_root, self.base.backup, expected_sequence=sequence,
            supervised=supervised, **self.journal.fixture.kwargs())

    def android_snapshot(self):
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for root in (self.base.work, self.base.saved.parent, self.base.misc, self.journal.fixture.state)
                for p in root.rglob('*')}

    def test_exact_staged_save_completed_without_android_changes_or_private_export(self):
        before = self.android_snapshot()
        candidate = (self.pending.stat().st_ino, self.pending.read_bytes())
        result = self.complete()
        self.assertEqual(self.android_snapshot(), before)
        self.assertEqual((self.journal.record.stat().st_ino, self.journal.record.read_bytes()), candidate)
        self.assertFalse(self.pending.exists())
        self.assertEqual(result['sequence'], 1)
        self.assertTrue(result['pendingRecordFinalized'])
        self.assertTrue(result['recoveryJournalChanged'])
        self.assertEqual(result['nextAction'], {'component': 'savedLayer', 'operation': 'restore-original'})
        self.assertFalse(self.fixture.status()['checkpointRequired'])
        for flag in ('configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged', 'servicesActivated',
                     'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed', 'installable',
                     'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified'):
            self.assertFalse(result[flag])
        self.assertNotIn(str(self.base.root), json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)

    def test_stale_sequence_does_not_publish_or_remove_pending_record(self):
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete(1)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_partial_wrong_binding_sequence_or_unobserved_record_preserved(self):
        original = self.pending.read_bytes()
        value = json.loads(original)
        candidates = [b'{', b'generated incomplete', b' ' * 16385,
                      original.replace(b'"version": 1', b'"version": 1,"version": 1')]
        for change in ({'sequence': 2}, {'dataInode': value['dataInode'] + 1}, {'version': True},
                       {'configurationDigest': 'f' * 64}, {'nextAction': None},
                       {'states': {**value['states'], 'savedLayer': 'restored'}}, {'extra': 'generated'}):
            candidates.append((json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n').encode())
        for candidate in candidates:
            self.pending.write_bytes(candidate)
            before = self.fixture.snapshot()
            with self.assertRaises(ValueError):
                self.complete()
            self.assertEqual(self.fixture.snapshot(), before)

    def test_no_observed_progress_refuses_even_canonical_pending_record(self):
        retained = self.base.misc / 'pending-droiddeck-allocator-machine-restored'
        retained.rename(self.base.misc / 'waydroid_settings')
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_out_of_order_actual_progress_is_not_committed(self):
        self.journal.exchange_original('configuration')
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_links_modes_and_writable_namespace_refuse(self):
        link = self.journal.parent / 'linked-candidate'
        os.link(self.pending, link)
        with self.assertRaises(ValueError):
            self.complete()
        link.unlink()
        self.pending.chmod(0o644)
        with self.assertRaises(ValueError):
            self.complete()
        self.pending.chmod(0o600)
        self.journal.final.chmod(0o777)
        with self.assertRaises(ValueError):
            self.complete()

    def test_extra_inventory_and_partial_initialization_preserved(self):
        foreign = self.journal.final / 'foreign'
        foreign.write_bytes(b'generated sentinel')
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)
        foreign.unlink()
        self.journal.pending.mkdir(mode=0o700)
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_supervisor_failure_before_publication_preserves_both_records(self):
        before = self.fixture.snapshot()
        calls = 0
        def deny():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.complete(supervised=deny)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_postpublication_timeout_reports_uncertainty_and_new_status(self):
        def timeout():
            if not self.pending.exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.complete(supervised=timeout)
        self.assertEqual(self.fixture.status()['sequence'], 1)
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_failed_replace_preserves_records_until_explicit_completion(self):
        before = self.fixture.snapshot()
        with mock.patch.object(P.os, 'replace', side_effect=OSError('generated replace refused')), self.assertRaises(OSError):
            self.complete()
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(self.complete()['sequence'], 1)

    def test_postpublication_fsync_failure_retains_new_record_and_reports_uncertainty(self):
        flush = P.os.fsync
        def fail_after_publication(fd):
            if not self.pending.exists():
                raise OSError('generated post-publication durability failure')
            return flush(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail_after_publication), self.assertRaises(P.PromotionUncertain):
            self.complete()
        self.assertEqual(self.fixture.status()['sequence'], 1)

    def test_pending_or_old_inode_replacement_before_publication_refuses(self):
        for target in (self.pending, self.journal.record):
            original = self.journal.record.read_bytes()
            calls = 0
            def race():
                nonlocal calls
                calls += 1
                if calls == 2:
                    alternate = self.journal.final / 'replacement'
                    alternate.write_bytes(target.read_bytes())
                    alternate.chmod(0o600)
                    alternate.replace(target)
            with self.assertRaises(ValueError):
                self.complete(supervised=race)
            self.assertEqual(self.journal.record.read_bytes(), original)
            self.assertTrue(self.pending.exists())

    def test_continuous_shared_lock_through_publication_and_return(self):
        import fcntl
        calls = 0
        def supervised():
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            fd = os.open(self.journal.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        self.complete(supervised=supervised)
        self.assertEqual(calls, 3)

    def test_controller_requires_explicit_completion_then_resumes_without_repeat_action(self):
        args = (self.journal.copier, self.journal.parent, self.base.cfg, self.base.fixture.metadata_root, self.base.backup)
        kwargs = self.journal.fixture.kwargs()
        before = self.fixture.snapshot()
        with self.assertRaises(ValueError):
            P._machine_gpu_recovery_run(*args, expected_sequence=0, supervised=lambda: None, **kwargs)
        self.assertEqual(self.fixture.snapshot(), before)
        self.complete()
        result = P._machine_gpu_recovery_run(*args, expected_sequence=1, supervised=lambda: None, **kwargs)
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints'], result['sequence']), (4, 4, 5))
        self.assertEqual(result['stage'], 'settled')


if __name__ == '__main__':
    unittest.main()
