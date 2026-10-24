# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('component_receipt_fixtures', Path(__file__).with_name('machine_gpu_configuration_test.py'))
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
P = C.P
ROLES = ('configuration', 'saved-layer')


class ReceiptBoundary(unittest.TestCase):
    def test_closed_role_digest_supervisor_and_no_cli(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_hold_machine_gpu_transaction', side_effect=AssertionError('must not inspect')):
            for kwargs in ({}, {'role': 'configuration', 'expected_manifest_sha256': 'a' * 64},
                           {'role': 'allocator', 'expected_manifest_sha256': 'a' * 64, 'supervised': lambda: None},
                           {'role': 'configuration', 'expected_manifest_sha256': True, 'supervised': lambda: None}):
                with self.assertRaises(ValueError):
                    P._machine_gpu_finish_component_receipt(mock.Mock(), **kwargs)
            with self.assertRaises(ValueError):
                P.main(['--finish-machine-gpu-component-receipt'])

    def test_active_guest_refuses_before_private_reads(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._machine_gpu_finish_component_receipt(mock.Mock(), role='configuration', expected_manifest_sha256='a' * 64, supervised=lambda: None)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated component receipt fixtures required')
class ComponentReceipts(unittest.TestCase):
    def make(self, role, *, stage=True):
        fixture = C.T.PrepareTransaction()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        state = fixture.parent / C.T.STATE
        current = fixture.cfg if role == 'configuration' else fixture.saved
        on, off, receipt_name = (('pending-droiddeck-machine-gpu.cfg', 'pending-droiddeck-machine-gpu-restore.cfg', 'configuration.json')
            if role == 'configuration' else ('pending-droiddeck-layer-machine-on', 'pending-droiddeck-layer-machine-off', 'saved-layer.json'))
        value = SimpleNamespace(fixture=fixture, state=state, current=current, on=current.parent / on,
            off=current.parent / off, final=state / receipt_name, pending=state / ('pending-' + receipt_name), role=role,
            original=(current.stat().st_ino, current.read_bytes()), manifest_sha=hashlib.sha256((state / 'manifest.json').read_bytes()).hexdigest(),
            args=(fixture.copier, fixture.parent, fixture.cfg, fixture.fixture.metadata_root, fixture.backup),
            kwargs={'offline': lambda stack, copier: fixture.fixture.admit(stack), 'profiles': fixture.profiles, 'verify_codecs': lambda _: None})
        if stage:
            with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated receipt publication interrupted')), self.assertRaises(OSError):
                self.change(value)
        return value

    def change(self, value, *, restore=False, supervised=lambda: None):
        return P._machine_gpu_owned_exchange(*value.args, role=value.role, restore=restore, supervised=supervised, **value.kwargs)

    def complete(self, value, *, supervised=lambda: None, digest=None):
        return P._machine_gpu_finish_component_receipt(*value.args, role=value.role,
            expected_manifest_sha256=value.manifest_sha if digest is None else digest, supervised=supervised, **value.kwargs)

    def snapshot(self, value, *, android_only=False):
        f = value.fixture
        roots = (f.work, f.saved.parent, f.misc) if android_only else (f.root,)
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for root in roots for p in root.rglob('*')}

    def test_atomic_writer_leaves_complete_pending_receipt_and_unselected_candidate(self):
        for role in ROLES:
            value = self.make(role)
            self.assertTrue(value.pending.exists())
            self.assertFalse(value.final.exists())
            self.assertTrue(value.on.exists())
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)
            self.assertEqual(json.loads(value.pending.read_bytes())['candidateInode'], value.on.stat().st_ino)
            with self.assertRaises(ValueError):
                self.change(value)

    def test_completion_changes_only_metadata_then_owned_recovery_retains_candidate(self):
        for role in ROLES:
            value = self.make(role)
            before = self.snapshot(value, android_only=True)
            pending = (value.pending.stat().st_ino, value.pending.read_bytes())
            result = self.complete(value)
            self.assertEqual(self.snapshot(value, android_only=True), before)
            self.assertEqual((value.final.stat().st_ino, value.final.read_bytes()), pending)
            self.assertFalse(value.pending.exists())
            self.assertTrue(result['componentReceiptPublished'])
            self.assertFalse(result['componentSelected'])
            self.assertNotIn(str(value.fixture.root), json.dumps(result))
            self.assertNotIn('generated-private-marker', json.dumps(result))
            for flag in ('configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged', 'servicesActivated',
                         'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed', 'installable',
                         'readyForActivation', 'accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified'):
                self.assertFalse(result[flag])
            with ExitStack() as stack:
                context = P._hold_machine_gpu_compound(value.args[0], stack, *value.args[1:], **value.kwargs)
                self.assertEqual(context['states']['configuration' if role == 'configuration' else 'savedLayer'], 'staged')
            P._machine_gpu_recovery_checkpoint(*value.args, initialize=True, supervised=lambda: None, **value.kwargs)
            recovered = P._machine_gpu_recovery_run(*value.args, expected_sequence=0, supervised=lambda: None, **value.kwargs)
            self.assertEqual((recovered['actionsPerformed'], recovered['progressCheckpoints']), (1, 1))
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)
            self.assertTrue(value.off.exists())

    def test_interrupted_partial_receipt_write_never_creates_truncated_final(self):
        for role in ROLES:
            value = self.make(role, stage=False)
            write = value.fixture.copier.write_all
            def fail(fd, data):
                if b'"kind": "protected-machine-gpu-' in data:
                    write(fd, data[:17])
                    raise OSError('generated partial receipt write')
                return write(fd, data)
            with mock.patch.object(value.fixture.copier, 'write_all', side_effect=fail), self.assertRaises(OSError):
                self.change(value)
            self.assertFalse(value.final.exists())
            self.assertTrue(value.pending.exists())
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)
            before = self.snapshot(value)
            with self.assertRaises(ValueError):
                self.complete(value)
            self.assertEqual(self.snapshot(value), before)

    def test_wrong_receipt_schema_binding_candidate_or_encoding_is_preserved(self):
        for role in ROLES:
            value = self.make(role)
            original = value.pending.read_bytes()
            parsed = json.loads(original)
            candidates = [b'{', original.replace(b'"version": 1', b'"version": 1,"version": 1')]
            for change in ({'version': True}, {'transactionManifestSha256': '0' * 64}, {'candidateSha256': 'f' * 64},
                           {'candidateInode': parsed['candidateInode'] + 1}, {'extra': 'generated'}):
                candidates.append((json.dumps({**parsed, **change}, sort_keys=True, indent=2) + '\n').encode())
            for encoded in candidates:
                value.pending.write_bytes(encoded)
                before = self.snapshot(value)
                with self.assertRaises(ValueError):
                    self.complete(value)
                self.assertEqual(self.snapshot(value), before)

    def test_foreign_same_bytes_candidate_inode_refuses(self):
        for role in ROLES:
            value = self.make(role)
            alternate = value.on.parent / 'replacement'
            alternate.write_bytes(value.on.read_bytes())
            alternate.chmod(value.on.stat().st_mode & 0o777)
            alternate.replace(value.on)
            before = self.snapshot(value)
            with self.assertRaises(ValueError):
                self.complete(value)
            self.assertEqual(self.snapshot(value), before)

    def test_stale_digest_or_changed_baseline_refuses_without_publication(self):
        for role in ROLES:
            value = self.make(role)
            before = self.snapshot(value)
            with self.assertRaises(ValueError):
                self.complete(value, digest='0' * 64)
            self.assertEqual(self.snapshot(value), before)
            value.current.write_bytes(value.current.read_bytes() + b'generated changed baseline')
            before = self.snapshot(value)
            with self.assertRaises(ValueError):
                self.complete(value)
            self.assertEqual(self.snapshot(value), before)

    def test_links_modes_and_retained_history_refuse(self):
        for role in ROLES:
            value = self.make(role)
            link = value.state / 'linked'
            os.link(value.pending, link)
            with self.assertRaises(ValueError):
                self.complete(value)
            link.unlink()
            value.pending.chmod(0o644)
            with self.assertRaises(ValueError):
                self.complete(value)
            value.pending.chmod(0o600)
            value.off.write_bytes(b'generated foreign retained candidate')
            before = self.snapshot(value)
            with self.assertRaises(ValueError):
                self.complete(value)
            self.assertEqual(self.snapshot(value), before)

    def test_conflicting_final_or_unrelated_inventory_is_not_overwritten(self):
        for role in ROLES:
            value = self.make(role)
            for target in (value.final, value.state / 'foreign'):
                target.write_bytes(b'generated sentinel')
                target.chmod(0o600)
                before = self.snapshot(value)
                with self.assertRaises(ValueError):
                    self.complete(value)
                self.assertEqual(self.snapshot(value), before)
                target.unlink()

    def test_supervisor_refusal_before_publication_preserves_all_files(self):
        for role in ROLES:
            value = self.make(role)
            before = self.snapshot(value)
            calls = 0
            def deny():
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise subprocess.TimeoutExpired('generated supervisor', 5)
            with self.assertRaises(subprocess.TimeoutExpired):
                self.complete(value, supervised=deny)
            self.assertEqual(self.snapshot(value), before)

    def test_postpublication_timeout_leaves_unselected_owned_candidate_recoverable(self):
        for role in ROLES:
            value = self.make(role)
            def timeout():
                if value.final.exists():
                    raise subprocess.TimeoutExpired('generated supervisor', 5)
            with self.assertRaises(P.PromotionUncertain):
                self.complete(value, supervised=timeout)
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)
            self.change(value, restore=True)
            self.assertTrue(value.off.exists())

    def test_postpublication_fsync_failure_preserves_owned_receipt(self):
        for role in ROLES:
            value = self.make(role)
            flush = P.os.fsync
            def fail(fd):
                if value.final.exists():
                    raise OSError('generated receipt durability failure')
                return flush(fd)
            with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
                self.complete(value)
            self.assertTrue(value.final.exists())
            self.assertFalse(value.pending.exists())
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)

    def test_failed_exclusive_publish_preserves_staged_receipt_until_explicit_retry(self):
        for role in ROLES:
            value = self.make(role)
            before = self.snapshot(value)
            with mock.patch.object(P, 'rename_exclusive', side_effect=OSError('generated rename refused')), self.assertRaises(OSError):
                self.complete(value)
            self.assertEqual(self.snapshot(value), before)
            self.assertTrue(self.complete(value)['componentReceiptPublished'])

    def test_held_candidate_or_receipt_replacement_before_publication_refuses(self):
        for role in ROLES:
            for which in ('on', 'pending'):
                value = self.make(role)
                target = getattr(value, which)
                calls = 0
                def race():
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        alternate = target.parent / 'replacement'
                        alternate.write_bytes(target.read_bytes())
                        alternate.chmod(target.stat().st_mode & 0o777)
                        alternate.replace(target)
                with self.assertRaises(ValueError):
                    self.complete(value, supervised=race)
                self.assertTrue(value.pending.exists())
                self.assertFalse(value.final.exists())

    def test_raced_final_receipt_is_preserved_by_exclusive_publication(self):
        for role in ROLES:
            value = self.make(role)
            rename = P.rename_exclusive
            def race(directory, source, target):
                value.final.write_bytes(b'generated foreign receipt')
                value.final.chmod(0o600)
                return rename(directory, source, target)
            with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
                self.complete(value)
            self.assertEqual(value.final.read_bytes(), b'generated foreign receipt')
            self.assertTrue(value.pending.exists())

    def test_shared_lock_is_held_through_completion(self):
        import fcntl
        for role in ROLES:
            value = self.make(role)
            calls = 0
            def supervised():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return
                fd = os.open(value.state, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            self.complete(value, supervised=supervised)
            self.assertEqual(calls, 4)

    def test_writer_postreceipt_timeout_does_not_select_gpu_candidate(self):
        for role in ROLES:
            value = self.make(role, stage=False)
            def timeout():
                if value.final.exists():
                    raise subprocess.TimeoutExpired('generated after receipt publication', 5)
            with self.assertRaises(P.PromotionUncertain):
                self.change(value, supervised=timeout)
            self.assertEqual((value.current.stat().st_ino, value.current.read_bytes()), value.original)
            self.assertTrue(value.final.exists())
            self.assertFalse(value.pending.exists())
            self.change(value, restore=True)


if __name__ == '__main__':
    unittest.main()
