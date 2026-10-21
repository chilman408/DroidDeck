# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('allocator_receipt_fixtures', Path(__file__).with_name('machine_gpu_allocator_test.py'))
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)
P = A.P


class AllocatorReceiptBoundary(unittest.TestCase):
    def test_explicit_digest_supervisor_and_no_cli(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_transaction', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'expected_manifest_sha256': 'a' * 64},
                           {'expected_manifest_sha256': True, 'supervised': lambda: None},
                           {'expected_manifest_sha256': 'A' * 64, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_finish_allocator_receipt(mock.Mock(), **kwargs)
            with self.assertRaises(ValueError):
                P.main(['--finish-machine-gpu-allocator-receipt'])

    def test_active_guest_refuses_before_private_reads(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._machine_gpu_finish_allocator_receipt(mock.Mock(), expected_manifest_sha256='a' * 64, supervised=lambda: None)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated allocator receipt fixtures required')
class AllocatorReceipts(unittest.TestCase):
    def make(self, *, stage=True):
        f = A.MachineAllocator()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.args = (f.copier, f.parent, f.fixture.cfg, f.fixture.fixture.metadata_root, f.fixture.backup)
        f.pending = f.state / 'pending-mesa-allocator-machine'
        f.final = f.state / 'mesa-allocator-machine'
        f.pending_receipt = f.pending / 'receipt.json'
        f.manifest_sha = hashlib.sha256(f.baseline).hexdigest()
        if stage:
            with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated receipt publication interrupted')), self.assertRaises(OSError):
                f.change()
        return f

    def complete(self, f, *, digest=None, supervised=lambda: None):
        return P._machine_gpu_finish_allocator_receipt(*f.args,
            expected_manifest_sha256=f.manifest_sha if digest is None else digest, supervised=supervised, **f.kwargs())

    def snapshot(self, f, *, android_only=False):
        roots = (f.fixture.work, f.fixture.saved.parent, f.misc) if android_only else (f.fixture.root,)
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for root in roots for p in root.rglob('*')}

    def test_producer_interruption_retains_pending_receipt_and_never_selects(self):
        f = self.make()
        self.assertTrue(f.pending_receipt.exists())
        self.assertFalse(f.final.exists())
        self.assertEqual(f.ready.read_bytes(), P.ALLOCATOR_CODE)
        self.assertFalse(f.file.exists())
        self.assertEqual(json.loads(f.pending_receipt.read_bytes())['fileInode'], f.ready.stat().st_ino)
        with self.assertRaises(ValueError):
            f.change()
        with ExitStack() as stack, self.assertRaises(ValueError):
            P._hold_machine_gpu_compound(f.copier, stack, *f.args[1:], **f.kwargs())
        f.unchanged_baseline()

    def test_metadata_only_completion_preserves_inodes_then_controller_retains_candidate(self):
        f = self.make()
        before = self.snapshot(f, android_only=True)
        directory_inode = f.pending.stat().st_ino
        receipt = (f.pending_receipt.stat().st_ino, f.pending_receipt.read_bytes())
        result = self.complete(f)
        self.assertEqual(self.snapshot(f, android_only=True), before)
        self.assertEqual(f.final.stat().st_ino, directory_inode)
        self.assertEqual((f.receipt.stat().st_ino, f.receipt.read_bytes()), receipt)
        self.assertFalse(f.pending.exists())
        self.assertTrue(result['componentReceiptPublished'])
        self.assertFalse(result['componentSelected'])
        self.assertNotIn(str(f.fixture.root), json.dumps(result))
        for flag in ('configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged', 'servicesActivated',
                     'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed', 'installable',
                     'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified',
                     'codecsVerified', 'recoveryJournalChanged'):
            self.assertFalse(result[flag])
        with ExitStack() as stack:
            ctx = P._hold_machine_gpu_compound(f.copier, stack, *f.args[1:], **f.kwargs())
            self.assertEqual(ctx['states'], {'allocator': 'staged', 'configuration': 'baseline', 'savedLayer': 'baseline'})
        inode = f.ready.stat().st_ino
        P._machine_gpu_recovery_checkpoint(*f.args, initialize=True, supervised=lambda: None, **f.kwargs())
        result = P._machine_gpu_recovery_run(*f.args, expected_sequence=0, supervised=lambda: None, **f.kwargs())
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints']), (1, 1))
        self.assertEqual(f.restored.stat().st_ino, inode)
        self.assertFalse(f.file.exists())
        f.unchanged_baseline()

    def test_completed_receipt_supports_existing_owned_restore_not_replay(self):
        f = self.make()
        self.complete(f)
        before = self.snapshot(f)
        with self.assertRaises(ValueError):
            self.complete(f)
        self.assertEqual(self.snapshot(f), before)
        f.change(restore=True)
        self.assertTrue(f.restored.exists())
        self.assertFalse(f.file.exists())
        f.unchanged_baseline()

    def test_partial_write_or_unreceipted_candidate_is_preserved(self):
        for partial in (True, False):
            f = self.make(stage=False)
            write = f.copier.write_all
            def fail(fd, data):
                if b'protected-mesa-allocator' in data:
                    if partial:
                        write(fd, data[:19])
                    raise OSError('generated partial receipt')
                return write(fd, data)
            with mock.patch.object(f.copier, 'write_all', side_effect=fail), self.assertRaises(OSError):
                f.change()
            self.assertFalse(f.final.exists())
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)

    def test_wrong_schema_scope_bindings_recipe_and_encoding_refuse(self):
        f = self.make()
        raw = f.pending_receipt.read_bytes()
        value = json.loads(raw)
        encoded = [b'{', raw + b' ', raw.replace(b'"version": 1', b'"version": 1,"version": 1'),
                   (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()]
        for changed in ({'version': True}, {'scope': 'proof'}, {'scope': 'activation'}, {'beforeAbsent': False},
                        {'dataInode': value['dataInode'] + 1}, {'miscInode': value['miscInode'] + 1},
                        {'fileInode': value['fileInode'] + 1}, {'candidateSha256': '0' * 64}, {'extra': False}):
            encoded.append((json.dumps({**value, **changed}, indent=2) + '\n').encode())
        for data in encoded:
            f.pending_receipt.write_bytes(data)
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)

    def test_stale_manifest_changed_baseline_and_wrong_candidate_refuse(self):
        f = self.make()
        before = self.snapshot(f)
        with self.assertRaises(ValueError):
            self.complete(f, digest='0' * 64)
        self.assertEqual(self.snapshot(f), before)
        for file in (f.marker, f.fixture.cfg, f.fixture.saved, f.ready):
            raw = file.read_bytes()
            file.write_bytes(raw + b' ')
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)
            file.write_bytes(raw)

    def test_same_bytes_foreign_candidate_inode_refuses(self):
        f = self.make()
        replacement = f.misc / 'replacement'
        replacement.write_bytes(P.ALLOCATOR_CODE)
        replacement.chmod(0o600)
        os.replace(replacement, f.ready)
        before = self.snapshot(f)
        with self.assertRaises(ValueError):
            self.complete(f)
        self.assertEqual(self.snapshot(f), before)

    def test_links_and_modes_are_not_repaired(self):
        for target in ('candidate', 'receipt', 'directory'):
            f = self.make()
            file = {'candidate': f.ready, 'receipt': f.pending_receipt, 'directory': f.pending}[target]
            file.chmod(0o777)
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)
        for target in ('candidate', 'receipt'):
            f = self.make()
            os.link(f.ready if target == 'candidate' else f.pending_receipt, f.parent / 'hardlink')
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)
        f = self.make()
        f.pending.rename(f.parent / 'displaced')
        f.pending.symlink_to(f.parent / 'displaced', target_is_directory=True)
        with self.assertRaises(OSError):
            self.complete(f)
        self.assertTrue(f.pending.is_symlink())

    def test_selected_retained_unreceipted_or_foreign_names_refuse(self):
        for parent, name in (('misc', 'waydroid_settings'), ('misc', 'pending-droiddeck-allocator-machine-restored'),
                             ('work', 'pending-droiddeck-machine-gpu.cfg'), ('saved', 'pending-droiddeck-layer-machine-on'),
                             ('pending', 'extra'), ('state', 'foreign'), ('state', 'mesa-allocator-machine')):
            f = self.make()
            directory = {'misc': f.misc, 'work': f.fixture.work, 'saved': f.fixture.saved.parent,
                         'pending': f.pending, 'state': f.state}[parent]
            (directory / name).write_bytes(b'generated foreign')
            before = self.snapshot(f)
            with self.assertRaises(ValueError):
                self.complete(f)
            self.assertEqual(self.snapshot(f), before)

    def test_guard_or_rename_failure_preserves_pending_material(self):
        f = self.make()
        before = self.snapshot(f)
        def deny():
            raise ValueError('generated supervisor refusal')
        with self.assertRaises(ValueError):
            self.complete(f, supervised=deny)
        self.assertEqual(self.snapshot(f), before)
        with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated rename failure')), self.assertRaises(OSError):
            self.complete(f)
        self.assertEqual(self.snapshot(f), before)

    def test_held_candidate_receipt_and_directory_replacement_refuse(self):
        for target in ('candidate', 'receipt', 'directory', 'manifest'):
            f = self.make()
            calls = 0
            def replace():
                nonlocal calls
                calls += 1
                if calls != 3:
                    return
                file = {'candidate': f.ready, 'receipt': f.pending_receipt, 'directory': f.pending, 'manifest': f.marker}[target]
                replacement = f.parent / 'replacement'
                if target == 'directory':
                    replacement.mkdir(mode=0o700)
                    (replacement / 'receipt.json').write_bytes(f.pending_receipt.read_bytes())
                    (replacement / 'receipt.json').chmod(0o600)
                    f.pending.rename(f.parent / 'displaced')
                else:
                    replacement.write_bytes(file.read_bytes())
                    replacement.chmod(0o600)
                os.replace(replacement, file)
            with self.assertRaises(ValueError):
                self.complete(f, supervised=replace)
            self.assertFalse(f.final.exists())
            self.assertTrue(f.ready.exists())

    def test_raced_final_directory_is_never_overwritten(self):
        f = self.make()
        rename = P.rename_exclusive
        def race(parent, source, final):
            f.final.mkdir(mode=0o700)
            (f.final / 'sentinel').write_bytes(b'generated foreign')
            return rename(parent, source, final)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(FileExistsError):
            self.complete(f)
        self.assertEqual((f.final / 'sentinel').read_bytes(), b'generated foreign')
        self.assertTrue(f.pending.exists())
        self.assertFalse(f.file.exists())

    def test_postpublication_timeout_or_fsync_failure_is_uncertain_and_recoverable(self):
        for failure in ('timeout', 'fsync'):
            f = self.make()
            before = self.snapshot(f, android_only=True)
            fsync = P.os.fsync
            def sync(fd):
                if failure == 'fsync' and f.final.exists():
                    raise OSError('generated durability failure')
                return fsync(fd)
            def guard():
                if failure == 'timeout' and f.final.exists():
                    raise subprocess.TimeoutExpired('generated supervisor', 5)
            with mock.patch.object(P.os, 'fsync', side_effect=sync), self.assertRaises(P.PromotionUncertain):
                self.complete(f, supervised=guard)
            self.assertEqual(self.snapshot(f, android_only=True), before)
            self.assertTrue(f.receipt.exists())
            f.change(restore=True)
            self.assertTrue(f.restored.exists())

    def test_postpublication_validation_failure_retains_uncertain_metadata(self):
        for target in ('candidate', 'receipt', 'directory'):
            f = self.make()
            changed = []
            def guard():
                if f.final.exists() and not changed:
                    changed.append(True)
                    file = {'candidate': f.ready, 'receipt': f.receipt, 'directory': f.final}[target]
                    file.chmod(0o777)
            with self.assertRaises(P.PromotionUncertain):
                self.complete(f, supervised=guard)
            self.assertTrue(f.final.exists())
            self.assertFalse(f.file.exists())

    def test_prepublication_fsync_failure_changes_no_names(self):
        f = self.make()
        before = self.snapshot(f)
        with mock.patch.object(P.os, 'fsync', side_effect=OSError('generated flush failure')), self.assertRaises(OSError):
            self.complete(f)
        self.assertEqual(self.snapshot(f), before)

    def test_shared_lock_is_held_through_publication(self):
        f = self.make()
        checked = []
        def guard():
            if not checked:
                checked.append('entered')
                return
            with ExitStack() as stack, self.assertRaises(BlockingIOError):
                f.hold(stack, allocator=True)
            checked.append('locked')
        self.complete(f, supervised=guard)
        self.assertGreaterEqual(checked.count('locked'), 3)
        with ExitStack() as stack:
            f.hold(stack, allocator=True)


if __name__ == '__main__':
    unittest.main()
