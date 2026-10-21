# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_status_fixtures', Path(__file__).with_name('machine_gpu_recovery_test.py'))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
P = R.P


class StatusBoundary(unittest.TestCase):
    def test_stopped_guard_precedes_reads_and_no_status_cli(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):
                P._machine_gpu_recovery_status(mock.Mock())
            with self.assertRaises(ValueError):
                P.main(['--machine-gpu-recovery-status'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated status fixtures required')
class RecoveryStatus(unittest.TestCase):
    def setUp(self):
        self.journal = R.RecoveryJournal()
        self.journal.setUp()
        self.addCleanup(self.journal.doCleanups)
        self.journal.checkpoint(initialize=True)
        self.base = self.journal.base
        self.record = self.journal.record

    def status(self):
        return P._machine_gpu_recovery_status(self.journal.copier, self.journal.parent, self.base.cfg,
            self.base.fixture.metadata_root, self.base.backup, **self.journal.fixture.kwargs())

    def snapshot(self):
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for root in (self.journal.parent, self.base.work, self.base.saved.parent, self.base.misc)
                for p in root.rglob('*')}

    def test_consistent_status_has_current_sequence_plan_and_no_writes_or_private_export(self):
        before = self.snapshot()
        result = self.status()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result['sequence'], 0)
        self.assertEqual(result['states'], result['observedStates'])
        self.assertEqual(result['nextAction'], {'component': 'allocator', 'operation': 'retain-candidate'})
        self.assertEqual(result['recordedNextAction'], result['nextAction'])
        self.assertFalse(result['checkpointRequired'])
        for key in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                'recoveryJournalChanged', 'configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged',
                'servicesActivated', 'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed',
                'installable', 'readyForActivation'):
            self.assertFalse(result[key])
        self.assertNotIn(str(self.base.root), json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)
        self.assertNotIn('generated-private-marker', json.dumps(result))

    def test_lagging_journal_suppresses_stale_action_until_explicit_checkpoint(self):
        for sequence, action in enumerate((self.journal.retain_allocator,
                lambda: self.journal.exchange_original('savedLayer'), lambda: self.journal.retain_pair('savedLayer'),
                lambda: self.journal.exchange_original('configuration'), lambda: self.journal.retain_pair('configuration'))):
            action()  # TEST-only generated file actions, never performed by status.
            before = self.snapshot()
            result = self.status()
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(result['checkpointRequired'])
            self.assertEqual(result['sequence'], sequence)
            self.assertNotEqual(result['states'], result['observedStates'])
            self.assertIsNone(result['nextAction'])
            saved = self.journal.checkpoint(expected_sequence=result['sequence'])
            current = self.status()
            self.assertFalse(current['checkpointRequired'])
            self.assertEqual(current['sequence'], saved['sequence'])
            self.assertEqual(current['nextAction'], saved['nextAction'])
        self.assertEqual(current['stage'], 'settled')
        self.assertIsNone(current['nextAction'])
        self.assertFalse(current['readyForActivation'])

    def test_out_of_order_observation_refuses_without_writing(self):
        self.journal.exchange_original('configuration')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.status()
        self.assertEqual(self.snapshot(), before)

    def test_missing_journal_is_not_initialized(self):
        self.record.unlink()
        self.journal.final.rmdir()
        before = self.snapshot()
        with self.assertRaises(FileNotFoundError):
            self.status()
        self.assertEqual(self.snapshot(), before)

    def test_partial_initialization_and_update_refuse_without_cleanup(self):
        self.journal.pending.mkdir(mode=0o700)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.status()
        self.assertEqual(self.snapshot(), before)
        self.journal.pending.rmdir()
        partial = self.journal.final / 'pending-recovery.json'
        partial.write_bytes(b'generated partial')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.status()
        self.assertEqual(self.snapshot(), before)

    def test_wrong_binding_or_encoding_refuses(self):
        original = self.record.read_bytes()
        value = json.loads(original)
        for change in ({'dataInode': value['dataInode'] + 1}, {'sequence': True}, {'nextAction': None},
                       {'configurationDigest': '0' * 64}, {'stage': 'settled'}):
            self.record.write_bytes((json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n').encode())
            with self.assertRaises(ValueError):
                self.status()
        self.record.write_bytes(original.replace(b'"version": 1', b'"version": 1,"version": 1'))
        with self.assertRaises(ValueError):
            self.status()

    def test_links_and_writable_namespaces_refuse(self):
        linked = self.journal.parent / 'linked-record'
        os.link(self.record, linked)
        with self.assertRaises(ValueError):
            self.status()
        linked.unlink()
        self.record.chmod(0o644)
        with self.assertRaises(ValueError):
            self.status()
        self.record.chmod(0o600)
        self.journal.final.chmod(0o777)
        with self.assertRaises(ValueError):
            self.status()

    def test_transaction_lock_conflict_does_not_return_status(self):
        import fcntl
        directory = os.open(self.journal.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, directory)
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(BlockingIOError):
            self.status()

    def test_record_replacement_or_namespace_race_after_observation_refuses(self):
        step = P._machine_recovery_step
        def race(previous, current):
            changed = step(previous, current)
            alternate = self.journal.final / 'replacement'
            alternate.write_bytes(self.record.read_bytes())
            alternate.chmod(0o600)
            alternate.replace(self.record)
            return changed
        with mock.patch.object(P, '_machine_recovery_step', side_effect=race), self.assertRaises(ValueError):
            self.status()

    def test_partial_metadata_race_after_observation_refuses(self):
        step = P._machine_recovery_step
        def race(previous, current):
            changed = step(previous, current)
            (self.journal.final / 'pending-recovery.json').write_bytes(b'generated partial')
            return changed
        with mock.patch.object(P, '_machine_recovery_step', side_effect=race), self.assertRaises(ValueError):
            self.status()

    def test_compound_change_after_observation_refuses(self):
        step = P._machine_recovery_step
        def race(previous, current):
            changed = step(previous, current)
            self.journal.retain_allocator()
            return changed
        with mock.patch.object(P, '_machine_recovery_step', side_effect=race), self.assertRaises(ValueError):
            self.status()


if __name__ == '__main__':
    unittest.main()
