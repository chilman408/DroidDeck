# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest import mock


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S = load('lifecycle_state', 'machine-gpu-lifecycle-state.py')
T = load('lifecycle_state_engine_fixtures', 'machine_gpu_lifecycle_test.py')
L = T.L


def initial(m):
    return {'kind': 'protected-machine-gpu-lifecycle-checkpoint', 'version': 1,
        'status': m.status, 'previousSha256': None, 'recoverySequence': None}


class StateBoundary(unittest.TestCase):
    def test_canonical_closed_initial_record(self):
        m = T.Model('prepared')
        raw = S.encode(initial(m))
        self.assertEqual(S.checkpoint(raw, L, m.expected, T.BOOT, m.now)[0], m.status)
        for changed in (b'{', raw + b' ', raw.replace(b'"version": 1', b'"version": 1,"version": 1'),
                        S.encode({**initial(m), 'extra': False}), S.encode({**initial(m), 'version': True}),
                        S.encode({**initial(m), 'previousSha256': 'f' * 64})):
            with self.assertRaises(ValueError):
                S.checkpoint(changed, L, m.expected, T.BOOT, m.now)

    def test_closed_names_and_preparation_boundary(self):
        for index in (-1, True, 16, 2**63):
            with self.assertRaises(ValueError):
                S.filename(index)
        with ExitStack() as stack, mock.patch.object(S.sys, 'platform', 'linux'), \
                mock.patch.object(S.os, 'geteuid', return_value=0, create=True), self.assertRaises(ValueError):
            S.prepare(mock.Mock(), stack, {}, L, {}, admit=None)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated lifecycle-state fixtures required')
class DurableState(unittest.TestCase):
    def setUp(self):
        self.C = load('lifecycle_storage_source_fixtures', 'machine_gpu_sources_test.py')
        self.f = self.C.ProtectedSources()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sources = self.f.hold(self.stack)
        self.m = T.Model('prepared')
        self.parent = self.f.root.parent / 'state'
        self.parent.mkdir(mode=0o700)
        self.root = self.parent / S.NAME
        self.proof = {'kind': 'protected-machine-gpu-recovery-status', 'sequence': 0, 'stage': 'settled',
            'checkpointRequired': False, 'nextAction': None, **self.m.expected,
            'states': dict.fromkeys(('allocator', 'savedLayer', 'configuration'), 'baseline')}
        self.proof['observedStates'] = dict(self.proof['states'])

    def kwargs(self):
        return {'admit': lambda: None, 'current_boot': lambda: self.m.boot, 'clock': lambda: self.m.now}

    def prepare(self):
        return S.prepare(self.f.copier, self.stack, self.sources, L, self.m.expected, self.parent, **self.kwargs())

    def open(self):
        return S.Store(self.f.copier, self.stack, self.sources, L, self.m.expected, self.parent,
            verify_recovery=lambda: copy.deepcopy(self.proof), **self.kwargs())

    def advance(self, store, phase):
        old = store.read()
        self.m.now += 1
        return store.advance(old, phase)

    def snapshot(self):
        return {p.name: (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None) for p in self.root.iterdir()}

    def test_new_prepare_reopen_and_immutable_forward_chain(self):
        self.prepare()
        store = self.open()
        before = self.snapshot()
        self.assertEqual(store.read()['stage'], 'prepared')
        self.assertEqual(self.snapshot(), before)
        for phase in ('stopping', 'installing', 'starting', 'waiting-h264', 'verifying-native', 'ready'):
            self.advance(store, phase)
        self.assertEqual(store.read()['revision'], 6)
        self.assertEqual(self.open().read(), store.read())
        self.assertEqual(self.snapshot()[S.filename(0)], before[S.filename(0)])
        self.assertEqual(len(list(self.root.iterdir())), 7)
        for p in self.root.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_preparation_replay_and_foreign_namespaces_are_not_overwritten(self):
        self.prepare()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.snapshot(), before)
        (self.parent / S.PENDING).mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.open()

    def test_stale_cas_illegal_phase_and_faked_completion_write_nothing(self):
        self.prepare()
        store = self.open()
        old = store.read()
        self.advance(store, 'stopping')
        before = self.snapshot()
        for expected, phase in ((old, 'installing'), (store.read(), 'ready'), (store.read(), 'restored')):
            with self.assertRaises(ValueError):
                store.advance(expected, phase)
            self.assertEqual(self.snapshot(), before)
        for changed in ({'sequence': True}, {'sequence': 1}, {'stage': 'recovering'}, {'checkpointRequired': True},
                        {'dataInode': 99}, {'states': {'allocator': 'selected'}}, {'kind': 'foreign'}):
            original = self.proof
            self.proof = {**original, **changed}
            with self.assertRaises(ValueError):
                store.mark_restored(store.read(), 0)
            self.assertEqual(self.snapshot(), before)
            self.proof = original

    def test_settled_proof_recorded_and_terminal_history_never_replays(self):
        self.prepare()
        store = self.open()
        old = store.read()
        self.m.now += 1
        restored = store.mark_restored(old, 0)
        self.assertEqual(restored['stage'], 'restored')
        envelope = json.loads((self.root / S.filename(1)).read_bytes())
        self.assertEqual(envelope['recoverySequence'], 0)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            store.mark_restored(restored, 0)
        self.assertEqual(self.snapshot(), before)

    def test_partial_append_is_preserved_and_ordinary_reader_refuses(self):
        self.prepare()
        store = self.open()
        write = self.f.copier.write_all
        def failed(fd, raw):
            write(fd, raw[:19])
            raise OSError('generated partial checkpoint write')
        with mock.patch.object(self.f.copier, 'write_all', side_effect=failed), self.assertRaises(OSError):
            store.advance(store.read(), 'stopping')
        self.assertTrue((self.root / S.RECORD).exists())
        self.assertFalse((self.root / S.filename(1)).exists())
        before = self.snapshot()
        with self.assertRaises(ValueError):
            store.read()
        self.assertEqual(self.snapshot(), before)

    def test_prepublication_failure_preserves_pending_complete_record(self):
        self.prepare()
        store = self.open()
        with mock.patch.object(store.p, 'rename_exclusive', side_effect=OSError('generated publish failure')), self.assertRaises(OSError):
            store.advance(store.read(), 'stopping')
        self.assertTrue((self.root / S.RECORD).exists())
        self.assertFalse((self.root / S.filename(1)).exists())
        self.assertEqual(json.loads((self.root / S.filename(0)).read_bytes())['status']['stage'], 'prepared')

    def test_postpublication_fsync_failure_is_uncertain_and_history_readable(self):
        self.prepare()
        store = self.open()
        old = store.read()
        fsync = S.os.fsync
        def failed(fd):
            if (self.root / S.filename(1)).exists():
                raise OSError('generated postpublication flush failure')
            return fsync(fd)
        with mock.patch.object(S.os, 'fsync', side_effect=failed), self.assertRaises(L.LifecycleUncertain):
            store.advance(old, 'stopping')
        self.assertEqual(store.read()['stage'], 'stopping')
        self.assertFalse((self.root / S.RECORD).exists())

    def test_raced_target_is_preserved_by_exclusive_publication(self):
        self.prepare()
        store = self.open()
        rename = store.p.rename_exclusive
        def raced(fd, source, target):
            (self.root / target).write_bytes(b'generated foreign sentinel')
            return rename(fd, source, target)
        with mock.patch.object(store.p, 'rename_exclusive', side_effect=raced), self.assertRaises(FileExistsError):
            store.advance(store.read(), 'stopping')
        self.assertEqual((self.root / S.filename(1)).read_bytes(), b'generated foreign sentinel')
        self.assertTrue((self.root / S.RECORD).exists())

    def test_postpublication_failure_keeps_original_record_anchor(self):
        self.prepare()
        store = self.open()
        old = store.read()
        fsync = S.os.fsync
        file = self.root / S.filename(1)
        def failed(fd):
            if file.exists():
                raise OSError('generated postpublication failure')
            return fsync(fd)
        with mock.patch.object(S.os, 'fsync', side_effect=failed), self.assertRaises(L.LifecycleUncertain):
            store.advance(old, 'stopping')
        replacement = self.parent / 'replacement'
        replacement.write_bytes(file.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, file)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            store.read()
        self.assertEqual(self.snapshot(), before)

    def test_initial_directory_substitution_is_never_modified_or_adopted(self):
        original_open = S.os.open
        substituted = []
        def raced(path, flags, *args, **kwargs):
            if path == S.PENDING or (isinstance(path, str) and path.startswith('pending-machine-gpu-lifecycle-attempt-')):
                pending = self.parent / path
                pending.rename(self.parent / 'original-pending')
                pending.mkdir(mode=0o755)
                (pending / 'foreign-sentinel').write_bytes(b'foreign directory')
                substituted.append(pending)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(S.os, 'open', side_effect=raced), self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(len(substituted), 1)
        pending = substituted[0]
        self.assertEqual(pending.stat().st_mode & 0o777, 0o755)
        self.assertEqual({p.name for p in pending.iterdir()}, {'foreign-sentinel'})
        self.assertEqual((pending / 'foreign-sentinel').read_bytes(), b'foreign directory')
        self.assertFalse(self.root.exists())

    def test_failed_postrename_stat_never_adopts_replacement(self):
        self.prepare()
        store = self.open()
        old = store.read()
        file = self.root / S.filename(1)
        fstat, failed = S.os.fstat, []
        def interrupted(fd):
            if file.exists() and not failed:
                failed.append(True)
                raise OSError('generated first postrename stat failure')
            return fstat(fd)
        with mock.patch.object(S.os, 'fstat', side_effect=interrupted), self.assertRaises(L.LifecycleUncertain):
            store.advance(old, 'stopping')
        replacement = self.parent / 'replacement'
        replacement.write_bytes(file.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, file)
        with self.assertRaises(ValueError):
            store.read()

    def test_failed_attempt_publication_preserves_orphan_and_blocks_retry(self):
        p = self.sources['load']('recovery')
        with mock.patch.dict(self.sources, {'load': lambda role: p}), \
                mock.patch.object(p, 'rename_exclusive', side_effect=OSError('generated pending publish failure')), \
                self.assertRaises(OSError):
            self.prepare()
        attempts = list(self.parent.glob('pending-machine-gpu-lifecycle-attempt-*'))
        self.assertEqual(len(attempts), 1)
        inode = attempts[0].stat().st_ino
        self.assertEqual(list(attempts[0].iterdir()), [])
        self.assertFalse((self.parent / S.PENDING).exists())
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(attempts[0].stat().st_ino, inode)

    def test_failed_pending_directory_flush_preserves_empty_namespace(self):
        fsync = S.os.fsync
        pending = self.parent / S.PENDING
        def failed(fd):
            if pending.exists():
                raise OSError('generated pending directory flush failure')
            return fsync(fd)
        with mock.patch.object(S.os, 'fsync', side_effect=failed), self.assertRaises(OSError):
            self.prepare()
        inode = pending.stat().st_ino
        self.assertEqual(list(pending.iterdir()), [])
        self.assertFalse(self.root.exists())
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(pending.stat().st_ino, inode)

    def test_orphan_attempt_refuses_existing_history_reader(self):
        self.prepare()
        store = self.open()
        before = self.snapshot()
        orphan = self.parent / 'pending-machine-gpu-lifecycle-attempt-generated'
        orphan.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            store.read()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(orphan.is_dir())

    def test_clock_regression_during_initial_write_preserves_pending(self):
        write = self.f.copier.write_all
        def regressed(fd, raw):
            write(fd, raw)
            self.m.now -= 1
        with mock.patch.object(self.f.copier, 'write_all', side_effect=regressed), self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.root.exists())
        self.assertTrue((self.parent / S.PENDING / S.filename(0)).exists())

    def test_invalid_clock_after_initial_publication_is_uncertain(self):
        fsync = S.os.fsync
        def invalid(fd):
            result = fsync(fd)
            if self.root.exists():
                self.m.now = float('nan')
            return result
        with mock.patch.object(S.os, 'fsync', side_effect=invalid), self.assertRaises(L.LifecycleUncertain):
            self.prepare()
        self.assertTrue((self.root / S.filename(0)).exists())

    def test_clock_regression_during_append_preserves_pending(self):
        self.prepare()
        store = self.open()
        old = store.read()
        write = self.f.copier.write_all
        def regressed(fd, raw):
            write(fd, raw)
            self.m.now -= 1
        with mock.patch.object(self.f.copier, 'write_all', side_effect=regressed), self.assertRaises(ValueError):
            store.advance(old, 'stopping')
        self.assertFalse((self.root / S.filename(1)).exists())
        self.assertTrue((self.root / S.RECORD).exists())

    def test_invalid_clock_after_append_publication_is_uncertain(self):
        self.prepare()
        store = self.open()
        old = store.read()
        fsync = S.os.fsync
        file = self.root / S.filename(1)
        def invalid(fd):
            result = fsync(fd)
            if file.exists():
                self.m.now = float('nan')
            return result
        with mock.patch.object(S.os, 'fsync', side_effect=invalid), self.assertRaises(L.LifecycleUncertain):
            store.advance(old, 'stopping')
        self.assertTrue(file.exists())

    def test_held_same_bytes_record_replacement_refuses(self):
        self.prepare()
        store = self.open()
        self.advance(store, 'stopping')
        file = self.root / S.filename(1)
        replacement = self.parent / 'replacement'
        replacement.write_bytes(file.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, file)
        with self.assertRaises(ValueError):
            store.read()

    def test_chain_hash_binding_mode_and_unknown_inventory_refuse(self):
        self.prepare()
        store = self.open()
        self.advance(store, 'stopping')
        file = self.root / S.filename(1)
        original = file.read_bytes()
        envelope = json.loads(original)
        for changed in ({'previousSha256': '0' * 64}, {'extra': False},
                        {'status': {**envelope['status'], 'dataInode': 99}}):
            file.write_bytes(S.encode({**envelope, **changed}))
            with self.assertRaises(ValueError):
                self.open()
        file.write_bytes(original)
        file.chmod(0o644)
        with self.assertRaises(ValueError):
            self.open()
        file.chmod(0o600)
        (self.root / 'unknown').write_bytes(b'generated')
        with self.assertRaises(ValueError):
            self.open()

    def test_directory_flock_and_reentry_prevent_overlap(self):
        self.prepare()
        store, other = self.open(), self.open()
        with store._locked():
            with self.assertRaises(ValueError):
                store.read()
            with self.assertRaises(BlockingIOError):
                other.read()
        self.assertEqual(other.read()['stage'], 'prepared')

    def test_boot_clock_and_source_loss_refuse_without_write(self):
        self.prepare()
        store = self.open()
        before = self.snapshot()
        self.m.now -= 1
        with self.assertRaises(ValueError):
            store.read()
        self.m.now += 1
        self.m.boot = '87654321-1234-1234-1234-123456789abc'
        with self.assertRaises(ValueError):
            store.read()
        self.m.boot = T.BOOT
        file = self.f.root / self.C.S.ROLES['health']
        file.write_bytes(file.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            store.read()
        self.assertEqual(self.snapshot(), before)

    def test_initial_partial_write_retains_pending_namespace(self):
        write = self.f.copier.write_all
        def failed(fd, raw):
            write(fd, raw[:9])
            raise OSError('generated initial write failure')
        with mock.patch.object(self.f.copier, 'write_all', side_effect=failed), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse(self.root.exists())
        self.assertTrue((self.parent / S.PENDING / S.filename(0)).exists())

    def test_initial_postpublication_flush_failure_retains_readable_history(self):
        fsync = S.os.fsync
        def failed(fd):
            if self.root.exists():
                raise OSError('generated initial postpublication flush failure')
            return fsync(fd)
        with mock.patch.object(S.os, 'fsync', side_effect=failed), self.assertRaises(L.LifecycleUncertain):
            self.prepare()
        self.assertEqual(self.open().read()['stage'], 'prepared')
        self.assertFalse((self.parent / S.PENDING).exists())

    def test_old_record_replacement_during_append_never_overwrites_it(self):
        self.prepare()
        store = self.open()
        old = store.read()
        file = self.root / S.filename(0)
        write = self.f.copier.write_all
        def changed(fd, raw):
            write(fd, raw)
            replacement = self.parent / 'replacement'
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, file)
        with mock.patch.object(self.f.copier, 'write_all', side_effect=changed), self.assertRaises(ValueError):
            store.advance(old, 'stopping')
        self.assertEqual(json.loads(file.read_bytes())['status'], old)
        self.assertFalse((self.root / S.filename(1)).exists())
        self.assertTrue((self.root / S.RECORD).exists())

    def test_watcher_completes_with_real_immutable_lifecycle_storage_and_controller(self):
        native = T.NativeLifecycle()
        self.addCleanup(native.doCleanups)
        with ExitStack() as stack:
            m, admitted, _peer, controller, _p = native.fixture(stack)
            parent = controller.base.root / 'lifecycle-state'
            parent.mkdir(mode=0o700)
            args = (self.f.copier, stack, m.sources, L, m.expected, parent)
            kwargs = {'admit': admitted['check'], 'current_boot': lambda: m.boot, 'clock': lambda: m.now}
            S.prepare(*args, **kwargs)
            store = S.Store(*args, verify_recovery=controller.status_fixture.status, **kwargs)
            store.advance(store.read(), 'recovery-required')
            watcher = m.make(admit=admitted['check'], read_status=store.read, mark_restored=store.mark_restored,
                recover=lambda guard: controller.run_recovery(supervised=guard))
            result = watcher.tick()
            self.assertEqual(result['action'], 'recovered')
            self.assertEqual(store.read()['stage'], 'restored')
            final = json.loads((parent / S.NAME / S.filename(2)).read_bytes())
            self.assertEqual(final['recoverySequence'], 5)
            self.assertEqual(json.loads(controller.journal.record.read_bytes())['sequence'], 5)
            self.assertTrue(all((parent / S.NAME / S.filename(i)).exists() for i in range(3)))


if __name__ == '__main__':
    unittest.main()
