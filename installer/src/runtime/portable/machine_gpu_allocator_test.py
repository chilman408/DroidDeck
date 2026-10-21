# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location('allocator_preparation_fixtures', Path(__file__).with_name('machine_gpu_prepare_test.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
P = T.P


class MachineAllocatorBoundary(unittest.TestCase):
    def test_no_machine_mutator_cli_or_unsupervised_internal_entry(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_allocator', None)), 'Machine allocator transition missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):
                P._machine_gpu_allocator(mock.Mock())
            for option in ('--machine-gpu-allocator', '--restore-machine-gpu-allocator'):
                with self.assertRaises(ValueError):
                    P.main([option])

    def test_busy_guard_precedes_prepared_manifest_read(self):
        self.assertTrue(callable(getattr(P, '_hold_machine_gpu_transaction', None)), 'Protected transaction reopening missing')
        with ExitStack() as stack, mock.patch.object(P.sys, 'platform', 'linux'), \
                mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated busy')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._hold_machine_gpu_transaction(mock.Mock(), stack)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated machine allocator fixtures required')
class MachineAllocator(unittest.TestCase):
    def setUp(self):
        self.fixture = T.PrepareTransaction()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.copier, self.parent = self.fixture.copier, self.fixture.parent
        self.state = self.parent / T.STATE
        self.marker = self.state / 'manifest.json'
        self.misc = self.fixture.misc
        self.file = self.misc / 'waydroid_settings'
        self.ready = self.misc / 'pending-droiddeck-allocator-machine-ready'
        self.restored = self.misc / 'pending-droiddeck-allocator-machine-restored'
        self.receipt = self.state / 'mesa-allocator-machine/receipt.json'
        self.baseline = self.marker.read_bytes()

    def kwargs(self):
        return {'offline': lambda stack, copier: self.fixture.fixture.admit(stack),
                'profiles': self.fixture.profiles, 'verify_codecs': lambda _: None}

    def hold(self, stack, **kw):
        return P._hold_machine_gpu_transaction(self.copier, stack, self.parent, self.fixture.cfg,
            self.fixture.fixture.metadata_root, self.fixture.backup, **self.kwargs(), **kw)

    def change(self, *, restore=False, supervised=lambda: None):
        return P._machine_gpu_allocator(self.copier, self.parent, self.fixture.cfg,
            self.fixture.fixture.metadata_root, self.fixture.backup, restore=restore, supervised=supervised, **self.kwargs())

    def unchanged_baseline(self):
        self.assertEqual(self.marker.read_bytes(), self.baseline)
        self.assertEqual(self.fixture.cfg.read_bytes(), self.fixture.baseline)
        self.assertEqual(self.fixture.saved.read_bytes(), self.fixture.original)

    def test_prepared_manifest_recomputed_from_independent_current_baseline_and_held(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            ctx['check']()
            self.assertEqual(ctx['manifest']['dataInode'], self.fixture.fixture.data.stat().st_ino)
            self.marker.write_bytes(self.baseline.replace(b'prepared', b'foreign!'))
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_closed_manifest_schema_generation_and_strict_encoding(self):
        original = json.loads(self.baseline)
        for changed in ({**original, 'version': True}, {**original, 'stage': 'ready'},
                        {**original, 'configurationDigest': 'f' * 64}, {**original, 'dataInode': original['dataInode'] + 1},
                        {**original, 'credential': 'generated'}, {**original, 'savedDocumentSha256': 'f' * 64}):
            self.marker.write_text(json.dumps(changed, sort_keys=True, indent=2) + '\n')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
        for encoded in (b'{"version":1,"version":1}', b'x' * 65537, self.baseline + b' '):
            self.marker.write_bytes(encoded)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)

    def test_linked_or_same_bytes_replaced_manifest_refuses(self):
        linked = self.parent / 'hardlink'
        os.link(self.marker, linked)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        linked.unlink()
        with ExitStack() as stack:
            ctx = self.hold(stack)
            replacement = self.parent / 'replacement'
            replacement.write_bytes(self.baseline)
            replacement.chmod(0o600)
            os.replace(replacement, self.marker)
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_foreign_inventory_pending_transaction_and_writable_state_refuse(self):
        for parent, name in ((self.state, 'foreign.pem'), (self.parent, T.PENDING)):
            file = parent / name
            file.write_bytes(b'generated')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            file.unlink()
        self.state.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_baseline_config_document_or_misc_identity_changes_refuse(self):
        for file, data in ((self.fixture.cfg, b'generated config'), (self.fixture.saved, b'generated document')):
            original = file.read_bytes()
            file.write_bytes(data)
            with self.assertRaises(ValueError):
                self.change()
            file.write_bytes(original)
        os.chown(self.misc, 1001, 9998)
        with self.assertRaises(ValueError):
            self.change()
        self.assertFalse(self.file.exists())

    def test_directory_lock_serializes_mutators_without_lock_file(self):
        with ExitStack() as first:
            self.hold(first)
            with ExitStack() as second, self.assertRaises(BlockingIOError):
                self.hold(second)
        self.assertEqual(set(p.name for p in self.state.iterdir()), {'manifest.json'})
        with ExitStack() as stack:
            self.hold(stack)

    def test_owned_apply_and_restore_retain_receipt_and_file_without_touching_other_state(self):
        result = self.change()
        self.assertEqual(result['scope'], 'machine')
        self.assertTrue(result['allocatorPresent'])
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'independentRecoveryArmed', 'servicesActivated', 'propertiesRegenerated', 'configurationChanged',
                     'savedPropertiesChanged', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])
        self.assertNotIn(str(self.parent), json.dumps(result))
        self.assertEqual(self.file.read_bytes(), P.ALLOCATOR_CODE)
        receipt = self.receipt.read_bytes()
        inode = self.file.stat().st_ino
        result = self.change(restore=True)
        self.assertFalse(result['allocatorPresent'])
        self.assertFalse(self.file.exists())
        self.assertEqual(self.restored.read_bytes(), P.ALLOCATOR_CODE)
        self.assertEqual(self.restored.stat().st_ino, inode)
        self.assertEqual(self.receipt.read_bytes(), receipt)
        self.unchanged_baseline()

    def test_foreign_settings_or_replay_never_overwrites(self):
        self.file.write_bytes(b'generated foreign')
        with self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.file.read_bytes(), b'generated foreign')
        self.file.unlink()
        self.change()
        with self.assertRaises(ValueError):
            self.change()
        self.change(restore=True)
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.unchanged_baseline()

    def test_foreign_same_bytes_allocator_inode_is_not_owned(self):
        self.change()
        replacement = self.misc / 'replacement'
        replacement.write_bytes(P.ALLOCATOR_CODE)
        replacement.chmod(0o600)
        os.replace(replacement, self.file)
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.assertTrue(self.file.exists())

    def test_receipt_scope_duplicates_and_unknown_fields_refuse_restore(self):
        self.change()
        original = self.receipt.read_bytes()
        for encoded in (original.replace(b'"scope": "machine"', b'"scope": "activation"'),
                        original.replace(b'"version": 1,', b'"version": 1,"version": 1,'),
                        original.replace(b'"version": 1,', b'"version": 1,"unknown": false,')):
            self.receipt.write_bytes(encoded)
            with self.assertRaises(ValueError):
                self.change(restore=True)
            self.assertTrue(self.file.exists())

    def test_owned_prepublication_candidate_can_restore_without_activation(self):
        rename = P.rename_exclusive
        def fail(parent, source, final='images'):
            if final == 'waydroid_settings':
                raise OSError('generated prepublication failure')
            return rename(parent, source, final)
        with mock.patch.object(P, 'rename_exclusive', side_effect=fail), self.assertRaises(OSError):
            self.change()
        self.assertTrue(self.ready.exists())
        self.assertFalse(self.file.exists())
        self.change(restore=True)
        self.assertTrue(self.restored.exists())
        self.unchanged_baseline()

    def test_supervisor_refusal_prevents_any_allocator_write(self):
        def deny():
            raise ValueError('generated supervisor not armed')
        with self.assertRaises(ValueError):
            self.change(supervised=deny)
        self.assertFalse(self.ready.exists())
        self.assertFalse(self.receipt.exists())
        self.unchanged_baseline()

    def test_manifest_change_during_write_prevents_visible_publication(self):
        write = self.copier.write_all
        def mutate(fd, data):
            write(fd, data)
            self.marker.write_bytes(self.baseline + b' ')
        with mock.patch.object(self.copier, 'write_all', side_effect=mutate), self.assertRaises(ValueError):
            self.change()
        self.assertFalse(self.file.exists())
        self.assertTrue(self.ready.exists())

    def test_postpublication_supervisor_timeout_is_uncertain_and_retains_owned_recovery(self):
        def timed_out():
            if self.file.exists():
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.change(supervised=timed_out)
        self.assertTrue(self.file.exists())
        self.assertTrue(self.receipt.exists())
        self.change(restore=True)
        self.unchanged_baseline()


if __name__ == '__main__':
    unittest.main()
