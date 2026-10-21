# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_initial_fixtures', Path(__file__).with_name('machine_gpu_recovery_test.py'))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
P = R.P


class InitialBoundary(unittest.TestCase):
    def test_no_cli_unsupervised_or_nonzero_sequence_completion(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_compound', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'expected_sequence': 0}, {'expected_sequence': True, 'supervised': lambda: None},
                           {'expected_sequence': 1, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_recovery_finish_initialization(mock.Mock(), **kwargs)
            with self.assertRaises(ValueError):
                P.main(['--finish-machine-gpu-recovery-initialization'])

    def test_internal_journal_modes_are_closed_and_mutually_exclusive(self):
        for kwargs in ({'_initial': 1}, {'_pending_update': 1}, {'_initial': True, '_pending_update': True}):
            with self.assertRaises(ValueError):
                P._hold_machine_gpu_recovery(mock.Mock(), mock.Mock(), {}, Path('/generated'), **kwargs)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated initial journal fixtures required')
class InitialCompletion(unittest.TestCase):
    def setUp(self):
        self.journal = R.RecoveryJournal()
        self.journal.setUp()
        self.addCleanup(self.journal.doCleanups)
        self.base = self.journal.base
        with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated initial save interrupted')), self.assertRaises(OSError):
            self.journal.checkpoint(initialize=True)
        self.pending = self.journal.pending
        self.record = self.pending / 'recovery.json'
        self.final = self.journal.final
        self.args = (self.journal.copier, self.journal.parent, self.base.cfg, self.base.fixture.metadata_root, self.base.backup)
        self.kwargs = self.journal.fixture.kwargs()

    def complete(self, supervised=lambda: None):
        return P._machine_gpu_recovery_finish_initialization(*self.args, expected_sequence=0, supervised=supervised, **self.kwargs)

    def snapshot(self, *, android_only=False):
        roots = (self.base.work, self.base.saved.parent, self.base.misc, self.journal.fixture.state)
        if not android_only:
            roots += (self.journal.parent,)
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for root in roots for p in root.rglob('*')}

    def status(self):
        return P._machine_gpu_recovery_status(*self.args, **self.kwargs)

    def test_publish_preserves_directory_record_inodes_and_all_android_files(self):
        before = self.snapshot(android_only=True)
        directory, record, encoded = self.pending.stat().st_ino, self.record.stat().st_ino, self.record.read_bytes()
        result = self.complete()
        self.assertEqual(self.snapshot(android_only=True), before)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.final.stat().st_ino, directory)
        self.assertEqual((self.final / 'recovery.json').stat().st_ino, record)
        self.assertEqual((self.final / 'recovery.json').read_bytes(), encoded)
        self.assertEqual(result['sequence'], 0)
        self.assertTrue(result['initialRecordPublished'])
        self.assertTrue(result['recoveryJournalChanged'])
        self.assertFalse(self.status()['checkpointRequired'])
        self.assertNotIn(str(self.base.root), json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)
        for flag in ('configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged', 'servicesActivated',
                     'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed', 'installable',
                     'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified'):
            self.assertFalse(result[flag])

    def test_truncated_duplicate_wrong_binding_sequence_and_unobserved_states_refuse(self):
        original = self.record.read_bytes()
        value = json.loads(original)
        unobserved = {**value['states'], 'allocator': 'restored'}
        candidates = [b'{', b' ' * 16385, original.replace(b'"version": 1', b'"version": 1,"version": 1')]
        for change in ({'sequence': 1}, {'version': True}, {'dataInode': value['dataInode'] + 1},
                       {'transactionManifestSha256': '0' * 64}, {'extra': 'generated'},
                       {'states': unobserved, 'nextAction': P._machine_recovery_plan(unobserved)}):
            candidates.append((json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n').encode())
        for candidate in candidates:
            self.record.write_bytes(candidate)
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.complete()
            self.assertEqual(self.snapshot(), before)

    def test_changed_actual_state_after_staging_is_not_accepted(self):
        self.journal.retain_allocator()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.snapshot(), before)

    def test_extra_inventory_is_preserved(self):
        (self.pending / 'foreign').write_bytes(b'generated sentinel')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.snapshot(), before)

    def test_links_modes_and_writable_directory_refuse(self):
        linked = self.journal.parent / 'linked-record'
        os.link(self.record, linked)
        with self.assertRaises(ValueError):
            self.complete()
        linked.unlink()
        self.record.chmod(0o644)
        with self.assertRaises(ValueError):
            self.complete()
        self.record.chmod(0o600)
        self.pending.chmod(0o777)
        with self.assertRaises(ValueError):
            self.complete()

    def test_existing_final_file_or_directory_is_never_replaced(self):
        for directory in (False, True):
            if directory:
                self.final.mkdir(mode=0o700)
                sentinel = self.final / 'foreign'
            else:
                sentinel = self.final
            sentinel.write_bytes(b'generated sentinel')
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.complete()
            self.assertEqual(self.snapshot(), before)
            sentinel.unlink()
            if directory:
                self.final.rmdir()

    def test_final_namespace_race_preserves_both_directories(self):
        rename = P.rename_exclusive
        def race(parent, source, target):
            self.final.mkdir(mode=0o700)
            (self.final / 'foreign').write_bytes(b'generated sentinel')
            return rename(parent, source, target)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.complete()
        self.assertEqual((self.final / 'foreign').read_bytes(), b'generated sentinel')
        self.assertTrue(self.record.exists())

    def test_supervisor_refusal_before_publication_preserves_staged_record(self):
        before = self.snapshot()
        calls = 0
        def deny():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.complete(supervised=deny)
        self.assertEqual(self.snapshot(), before)

    def test_postpublication_timeout_retains_usable_record_and_refuses_replay(self):
        def timeout():
            if self.final.exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.complete(supervised=timeout)
        self.assertEqual(self.status()['sequence'], 0)
        before = self.snapshot()
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.complete()
        self.assertEqual(self.snapshot(), before)

    def test_postpublication_fsync_failure_reports_uncertainty_without_deletion(self):
        flush = P.os.fsync
        def fail(fd):
            if self.final.exists():
                raise OSError('generated durability failure')
            return flush(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.complete()
        self.assertEqual(self.status()['sequence'], 0)

    def test_failed_exclusive_rename_can_be_explicitly_completed_later(self):
        before = self.snapshot()
        with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated rename refused')), self.assertRaises(OSError):
            self.complete()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.complete()['sequence'], 0)

    def test_staged_record_inode_replacement_before_publication_refuses(self):
        calls = 0
        def race():
            nonlocal calls
            calls += 1
            if calls == 2:
                alternate = self.pending / 'replacement'
                alternate.write_bytes(self.record.read_bytes())
                alternate.chmod(0o600)
                alternate.replace(self.record)
        with self.assertRaises(ValueError):
            self.complete(supervised=race)
        self.assertTrue(self.record.exists())
        self.assertFalse(self.final.exists())

    def test_pending_directory_replacement_is_preserved_and_refused(self):
        displaced = self.journal.parent / 'generated-displaced'
        calls = 0
        def race():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.pending.rename(displaced)
                self.pending.mkdir(mode=0o700)
                self.record.write_bytes((displaced / 'recovery.json').read_bytes())
                self.record.chmod(0o600)
        with self.assertRaises(ValueError):
            self.complete(supervised=race)
        self.assertTrue((displaced / 'recovery.json').exists())
        self.assertTrue(self.record.exists())
        self.assertFalse(self.final.exists())

    def test_shared_transaction_lock_is_continuous_until_return(self):
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

    def test_controller_requires_explicit_initial_completion_then_recovers(self):
        before = self.snapshot()
        with self.assertRaises(FileNotFoundError):
            P._machine_gpu_recovery_run(*self.args, expected_sequence=0, supervised=lambda: None, **self.kwargs)
        self.assertEqual(self.snapshot(), before)
        self.complete()
        result = P._machine_gpu_recovery_run(*self.args, expected_sequence=0, supervised=lambda: None, **self.kwargs)
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints'], result['sequence']), (5, 5, 5))

    def test_missing_pending_namespace_is_not_created(self):
        self.record.unlink()
        self.pending.rmdir()
        before = self.snapshot()
        with self.assertRaises(FileNotFoundError):
            self.complete()
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
