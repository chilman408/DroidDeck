# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('recovery_compound_fixtures', Path(__file__).with_name('machine_gpu_compound_test.py'))
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
P = C.P
ROLES = ('configuration', 'savedLayer', 'allocator')


class RecoveryOrder(unittest.TestCase):
    def test_all_64_states_plan_only_reverse_recovery_order_and_terminate(self):
        self.assertTrue(callable(getattr(P, '_machine_recovery_plan', None)), 'Recovery order missing')
        for combination in itertools.product(('baseline', 'staged', 'selected', 'restored'), repeat=3):
            states = dict(zip(ROLES, combination))
            for _ in range(6):
                plan = P._machine_recovery_plan(states)
                if plan is None:
                    break
                role = next(name for name in ('allocator', 'savedLayer', 'configuration') if states[name] not in ('baseline', 'restored'))
                self.assertEqual(plan['component'], role)
                target = 'staged' if role != 'allocator' and states[role] == 'selected' else 'restored'
                current = {**states, role: target}
                self.assertTrue(P._machine_recovery_step(states, current))
                self.assertFalse(P._machine_recovery_step(states, states))
                states = current
            self.assertIsNone(P._machine_recovery_plan(states))

    def test_closed_states_and_out_of_order_or_activation_changes_refuse(self):
        self.assertTrue(callable(getattr(P, '_machine_recovery_step', None)))
        selected = dict.fromkeys(ROLES, 'selected')
        for value in (None, {}, {**selected, 'password': 'generated'}, {**selected, 'configuration': True}, {**selected, 'allocator': 'running'}):
            with self.assertRaises(ValueError):
                P._machine_recovery_plan(value)
        for current in ({**selected, 'configuration': 'restored'}, {**selected, 'savedLayer': 'staged'},
                        {**selected, 'allocator': 'baseline'}, {**selected, 'allocator': 'staged'}, dict.fromkeys(ROLES, 'restored')):
            with self.assertRaises(ValueError):
                P._machine_recovery_step(selected, current)

    def test_no_cli_or_unsupervised_journal_write(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_recovery_checkpoint', None)), 'Durable recovery checkpoint missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_compound', side_effect=AssertionError('must not inspect')):
            with self.assertRaises(ValueError):
                P._machine_gpu_recovery_checkpoint(mock.Mock(), initialize=True)
            for option in ('--machine-gpu-recovery-checkpoint', '--start-machine-gpu-recovery'):
                with self.assertRaises(ValueError):
                    P.main([option])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated journal fixtures required')
class RecoveryJournal(unittest.TestCase):
    def setUp(self):
        self.fixture = C.CompoundOwnership()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = self.fixture.fixture
        self.parent = self.base.parent
        self.final = self.parent / 'machine-gpu-recovery'
        self.pending = self.parent / 'pending-machine-gpu-recovery'
        self.record = self.final / 'recovery.json'
        self.copier = self.fixture.copier
        for role in ROLES:
            self.fixture.select(role, 'selected')

    def checkpoint(self, *, initialize=False, expected_sequence=None, supervised=lambda: None):
        return P._machine_gpu_recovery_checkpoint(self.copier, self.parent, self.base.cfg,
            self.base.fixture.metadata_root, self.base.backup, initialize=initialize,
            expected_sequence=expected_sequence, supervised=supervised, **self.fixture.kwargs())

    def retain_allocator(self):
        (self.base.misc / 'waydroid_settings').rename(self.base.misc / 'pending-droiddeck-allocator-machine-restored')

    def exchange_original(self, role):
        current, on, _, _, _, _ = self.fixture.layout[role]
        temporary = current.parent / 'generated-swap'
        current.rename(temporary)
        (current.parent / on).rename(current)
        temporary.rename(current.parent / on)

    def retain_pair(self, role):
        current, on, off, _, _, _ = self.fixture.layout[role]
        (current.parent / on).rename(current.parent / off)

    def test_durable_ordered_progress_tracks_generated_changes_without_executing_them(self):
        before = {str(p): (p.stat().st_ino, p.read_bytes()) for parent in (self.base.work, self.base.saved.parent, self.base.misc)
                  for p in parent.iterdir() if p.is_file()}
        result = self.checkpoint(initialize=True)
        self.assertEqual(result['sequence'], 0)
        self.assertEqual(result['nextAction'], {'component': 'allocator', 'operation': 'retain-candidate'})
        after = {str(p): (p.stat().st_ino, p.read_bytes()) for parent in (self.base.work, self.base.saved.parent, self.base.misc)
                 for p in parent.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        for sequence, action in enumerate((self.retain_allocator, lambda: self.exchange_original('savedLayer'),
                lambda: self.retain_pair('savedLayer'), lambda: self.exchange_original('configuration'),
                lambda: self.retain_pair('configuration'))):
            action()  # Generated fixture operations, never executed by checkpoint production code.
            result = self.checkpoint(expected_sequence=sequence)
            self.assertEqual(result['sequence'], sequence + 1)
        self.assertEqual(result['stage'], 'settled')
        self.assertIsNone(result['nextAction'])
        self.assertEqual(self.base.cfg.read_bytes(), self.base.baseline)
        self.assertEqual(self.base.saved.read_bytes(), self.base.original)
        self.assertEqual(self.final.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.pending.exists())
        self.assertEqual(set(p.name for p in self.final.iterdir()), {'recovery.json'})
        self.assertNotIn(str(self.base.root), json.dumps(result))
        self.assertNotIn('generated-private-marker', self.record.read_text())
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'servicesActivated', 'propertiesRegenerated', 'configurationChanged', 'savedPropertiesChanged',
                     'allocatorOverrideChanged', 'androidOperationsExecuted', 'independentRecoveryArmed', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])

    def test_unchanged_observation_is_idempotent_without_replacing_record(self):
        self.checkpoint(initialize=True)
        before = (self.record.stat().st_ino, self.record.read_bytes())
        result = self.checkpoint(expected_sequence=0)
        self.assertFalse(result['recoveryJournalChanged'])
        self.assertEqual(result['sequence'], 0)
        self.assertEqual((self.record.stat().st_ino, self.record.read_bytes()), before)

    def test_stale_sequence_and_initialization_replay_refuse(self):
        self.checkpoint(initialize=True)
        for kwargs in ({'initialize': True}, {'expected_sequence': 1}, {'expected_sequence': True}, {}):
            with self.assertRaises(ValueError):
                self.checkpoint(**kwargs)

    def test_out_of_order_observed_change_does_not_commit(self):
        self.checkpoint(initialize=True)
        before = self.record.read_bytes()
        self.exchange_original('configuration')
        with self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)
        self.assertEqual(self.record.read_bytes(), before)

    def test_wrong_binding_schema_duplicate_fields_or_stage_refuse(self):
        self.checkpoint(initialize=True)
        original = self.record.read_bytes()
        value = json.loads(original)
        for change in ({'version': True}, {'sequence': -1}, {'transactionManifestSha256': 'f' * 64},
                       {'stage': 'settled'}, {'nextAction': None}, {'credential': 'generated'}):
            self.record.write_text(json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n')
            with self.assertRaises(ValueError):
                self.checkpoint(expected_sequence=0)
        self.record.write_bytes(original.replace(b'"version": 1', b'"version": 1,"version": 1'))
        with self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)

    def test_foreign_final_pending_and_extra_inventory_are_preserved(self):
        for directory in (self.final, self.pending):
            directory.mkdir(mode=0o700)
            (directory / 'foreign').write_bytes(b'generated sentinel')
            with self.assertRaises(ValueError):
                self.checkpoint(initialize=True)
            self.assertEqual((directory / 'foreign').read_bytes(), b'generated sentinel')
            (directory / 'foreign').unlink()
            directory.rmdir()
        self.checkpoint(initialize=True)
        (self.final / 'pending-recovery.json').write_bytes(b'generated partial')
        with self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)

    def test_linked_record_or_writable_journal_namespace_refuses(self):
        self.checkpoint(initialize=True)
        link = self.parent / 'linked'
        os.link(self.record, link)
        with self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)
        link.unlink()
        self.final.chmod(0o777)
        with self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)

    def test_supervisor_refusal_and_changed_metadata_prevent_publication(self):
        def deny():
            raise ValueError('generated supervisor unavailable')
        with self.assertRaises(ValueError):
            self.checkpoint(initialize=True, supervised=deny)
        self.assertFalse(self.pending.exists())
        write = self.copier.write_all
        def change(fd, data):
            write(fd, data)
            self.base.fixture.metadata.write_bytes(b'generated metadata changed')
        with mock.patch.object(self.copier, 'write_all', side_effect=change), self.assertRaises(ValueError):
            self.checkpoint(initialize=True)
        self.assertFalse(self.final.exists())
        self.assertTrue(self.pending.exists())

    def test_postpublication_timeout_is_uncertain_and_next_call_reads_durable_progress(self):
        self.checkpoint(initialize=True)
        self.retain_allocator()
        def timeout():
            if json.loads(self.record.read_bytes())['sequence'] == 1:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.checkpoint(expected_sequence=0, supervised=timeout)
        self.assertEqual(json.loads(self.record.read_bytes())['sequence'], 1)
        result = self.checkpoint(expected_sequence=1)
        self.assertEqual(result['sequence'], 1)
        self.assertFalse(result['recoveryJournalChanged'])

    def test_prepublication_update_failure_preserves_old_record_and_pending_for_inspection(self):
        self.checkpoint(initialize=True)
        before = self.record.read_bytes()
        self.retain_allocator()
        with mock.patch.object(P.os, 'replace', side_effect=OSError('generated atomic save failed')), self.assertRaises(OSError):
            self.checkpoint(expected_sequence=0)
        self.assertEqual(self.record.read_bytes(), before)
        self.assertTrue((self.final / 'pending-recovery.json').exists())

    def test_no_change_observation_rechecks_journal_inventory(self):
        self.checkpoint(initialize=True)
        def race(previous, current):
            (self.final / 'foreign').write_bytes(b'generated foreign')
            return False
        with mock.patch.object(P, '_machine_recovery_step', side_effect=race), self.assertRaises(ValueError):
            self.checkpoint(expected_sequence=0)

    def test_raced_final_directory_preserved_without_replacing_it(self):
        rename = P.rename_exclusive
        def race(parent, pending, final):
            self.final.mkdir(mode=0o700)
            (self.final / 'foreign').write_bytes(b'generated sentinel')
            return rename(parent, pending, final)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.checkpoint(initialize=True)
        self.assertEqual((self.final / 'foreign').read_bytes(), b'generated sentinel')
        self.assertTrue((self.pending / 'recovery.json').exists())

    def test_initial_postpublication_timeout_retains_usable_journal(self):
        def timeout():
            if self.final.exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.checkpoint(initialize=True, supervised=timeout)
        self.assertEqual(json.loads(self.record.read_bytes())['sequence'], 0)
        self.assertFalse(self.checkpoint(expected_sequence=0)['recoveryJournalChanged'])


if __name__ == '__main__':
    unittest.main()
