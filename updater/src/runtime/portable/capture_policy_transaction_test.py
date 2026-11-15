# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('policy_transaction', Path(__file__).with_name('capture-policy-transaction.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
NATIVE = sys.platform == 'linux' and os.geteuid() == 0 if hasattr(os, 'geteuid') else False


class SchemaTests(unittest.TestCase):
    def test_reader_compatible_closed_policy_and_private_errors(self):
        for mode in ('cpu', 'gpu'):
            self.assertEqual(P.parse_policy(P.policy_bytes(mode)), mode)
            self.assertEqual(P.parse_policy((' { "version": 1, "preprocessing": "' + mode + '" } \n').encode()), mode)
        for raw in (b'{"version":true,"preprocessing":"cpu"}', b'{"version":1.0,"preprocessing":"cpu"}',
                    b'{"version":1,"preprocessing":"cpu","preprocessing":"gpu"}', b'generated-private-sentinel',
                    b'\xff', b'x' * 257, b'{}', b'[]', b'null'):
            with self.assertRaisesRegex(ValueError, '^Invalid capture policy bytes$'):
                P.parse_policy(raw)
        for mode in (None, [], 1, True, 'auto'):
            with self.assertRaises(ValueError): P.policy_bytes(mode)

    def test_closed_canonical_journal_and_snapshot_schema(self):
        value = {'version': 1, 'kind': 'capture-policy-transaction', 'token': 'a' * 32, 'phase': 'prepared',
                 'mode': 'gpu', 'before': None, 'after': None, 'restored': None}
        self.assertEqual(P.journal_record(P.encode(value)), value)
        for changes in ({'phase': 'accepted'}, {'phase': []}, {'token': '../policy'}, {'version': True},
                        {'extra': 'private'}, {'before': {'hex': 'ab'}}):
            with self.assertRaisesRegex(ValueError, '^Invalid capture transaction journal$'):
                P.journal_record(P.encode({**value, **changes}))
        for raw in (json.dumps(value).encode(), P.encode(value) + b' ', b'generated-private-sentinel'):
            with self.assertRaisesRegex(ValueError, '^Invalid capture transaction journal$'): P.journal_record(raw)


@unittest.skipUnless(NATIVE, 'root Linux held-directory/flock/renameat2 tests')
class NativeTests(unittest.TestCase):
    def fixture(self, before=None):
        directory = tempfile.TemporaryDirectory(prefix='generated-policy-transaction-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        root.chmod(0o700)
        if before is not None:
            (root / P.POLICY).write_bytes(before)
            (root / P.POLICY).chmod(0o640)
            os.chown(root / P.POLICY, 0, 1)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.addCleanup(os.close, fd)
        return root, fd

    def prior(self):
        return b'{ "version": 1, "preprocessing": "cpu" }\n'

    def assert_restored(self, root, before):
        if before is None:
            self.assertFalse(os.path.lexists(root / P.POLICY))
        else:
            self.assertEqual((root / P.POLICY).read_bytes(), before)
            info = (root / P.POLICY).stat()
            self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid), (0o640, 0, 1))

    def test_accept_requires_proof_and_retained_receipt_allows_exact_rollback(self):
        for before in (None, self.prior()):
            with self.subTest(before=before is not None):
                root, fd = self.fixture(before)
                store = P.PolicyTransaction(fd)
                token = store.begin('gpu')
                self.assertEqual(P.parse_policy((root / P.POLICY).read_bytes()), 'gpu')
                with self.assertRaises(ValueError): store.accept('b' * 32, lambda: True)
                with self.assertRaises(ValueError): store.accept(token, lambda: False)
                self.assertEqual(store.inspect()['phase'], 'applying')
                store.accept(token, lambda: True)
                self.assertEqual(P.PolicyTransaction(fd).recover(), 'accepted')
                self.assertEqual(store.rollback(token), 'rolled-back')
                self.assertEqual(P.PolicyTransaction(fd).recover(), 'rolled-back')
                self.assert_restored(root, before)
                with self.assertRaises(ValueError): store.begin('cpu')

    def test_cpu_selection_and_pending_recovery_keep_prior_gpu_exact(self):
        before = b'{"preprocessing":"gpu","version":1}\n'
        root, fd = self.fixture(before)
        store = P.PolicyTransaction(fd)
        store.begin('cpu')
        self.assertEqual(P.parse_policy((root / P.POLICY).read_bytes()), 'cpu')
        self.assertEqual(P.PolicyTransaction(fd).recover(), 'rolled-back')
        self.assert_restored(root, before)

    def test_no_transaction_and_no_service_or_runtime_configuration_side_effects(self):
        root, fd = self.fixture()
        sentinel = root / 'runtime.json'
        sentinel.write_bytes(b'generated-configuration-sentinel')
        info = sentinel.stat()
        store = P.PolicyTransaction(fd)
        self.assertEqual(store.recover(), 'no-transaction')
        store.rollback(store.begin('gpu'))
        self.assertEqual(sentinel.read_bytes(), b'generated-configuration-sentinel')
        self.assertEqual(sentinel.stat().st_ino, info.st_ino)

    def test_foreign_edit_and_same_bytes_new_inode_never_overwritten(self):
        for mode in ('changed-bytes', 'same-bytes', 'same-inode', 'missing'):
            with self.subTest(mode=mode):
                root, fd = self.fixture(self.prior())
                store = P.PolicyTransaction(fd)
                token = store.begin('gpu')
                file = root / P.POLICY
                raw = file.read_bytes()
                if mode == 'same-inode':
                    file.write_bytes(raw)
                    os.utime(file, ns=(1, 2))
                else:
                    file.unlink()
                    if mode != 'missing':
                        file.write_bytes(raw if mode == 'same-bytes' else P.policy_bytes('cpu'))
                        file.chmod(0o644)
                current = file.read_bytes() if file.exists() else None
                for action in (lambda: store.accept(token, lambda: True), lambda: store.rollback(token), store.recover):
                    with self.assertRaises(ValueError): action()
                    self.assertEqual(file.read_bytes() if file.exists() else None, current)

    def test_symlinks_hardlinks_fifo_owner_mode_and_extended_metadata_rejected(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'owner', 'mode', 'xattr', 'journal-symlink'):
            with self.subTest(kind=kind):
                root, fd = self.fixture(self.prior())
                file = root / P.POLICY
                if kind == 'symlink': file.unlink(); file.symlink_to('runtime.json')
                if kind == 'hardlink': os.link(file, root / 'other')
                if kind == 'fifo': file.unlink(); os.mkfifo(file)
                if kind == 'owner': os.chown(file, 1000, 1000)
                if kind == 'mode': file.chmod(0o666)
                if kind == 'xattr': os.setxattr(file, 'user.generated', b'test')
                if kind == 'journal-symlink': (root / P.JOURNAL).symlink_to('runtime.json')
                with self.assertRaises((ValueError, OSError)): P.PolicyTransaction(fd).begin('gpu')

    def test_journal_replacement_after_publication_prevents_policy_publication(self):
        for change in ('missing', 'same-bytes', 'malformed'):
            root, fd = self.fixture()
            def interfere(event):
                if event != 'journal-durable-prepared': return
                file = root / P.JOURNAL
                raw = file.read_bytes()
                file.rename(root / 'generated-prior-journal')
                if change != 'missing':
                    file.write_bytes(raw if change == 'same-bytes' else b'generated-private-sentinel')
                    file.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'Concurrent'):
                P.PolicyTransaction(fd, checkpoint=interfere).begin('gpu')
            self.assertFalse(os.path.lexists(root / P.POLICY))

    def test_flock_reentry_and_changed_guard_stop_changes(self):
        import fcntl
        root, fd = self.fixture()
        other = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): P.PolicyTransaction(fd).begin('gpu')
        finally:
            os.close(other)
        store = P.PolicyTransaction(fd)
        token = store.begin('gpu')
        with self.assertRaisesRegex(ValueError, 'reentry'): store.accept(token, store.recover)
        def changed(): raise ValueError('generated ancestor replacement')
        store.guard = changed
        with self.assertRaisesRegex(ValueError, 'ancestor'): store.rollback(token)
        self.assertEqual(P.parse_policy((root / P.POLICY).read_bytes()), 'gpu')

    def crash_case(self, before, operation, target):
        root, fd = self.fixture(before)
        token = None
        if operation != 'begin':
            store = P.PolicyTransaction(fd)
            token = store.begin('gpu')
            if operation == 'rollback': store.accept(token, lambda: True)
        pid = os.fork()
        if pid == 0:
            count = 0
            def crash(event):
                nonlocal count
                count += 1
                if count == target: os._exit(77)
            try:
                store = P.PolicyTransaction(fd, checkpoint=crash)
                if operation == 'begin': store.begin('gpu')
                elif operation == 'accept': store.accept(token, lambda: True)
                else: store.rollback(token)
                os._exit(0)
            except BaseException:
                os._exit(99)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 77)
        store = P.PolicyTransaction(fd)
        result = store.recover()
        if result == 'accepted':
            # Death before rollback intent publication legitimately keeps accepted state.
            self.assertIn(operation, ('accept', 'rollback'))
            store.rollback(token)
        self.assert_restored(root, before)
        return result

    def test_process_death_at_every_publication_and_durability_boundary(self):
        counts = {}
        for before in (None, self.prior()):
            for operation in ('begin', 'accept', 'rollback'):
                root, fd = self.fixture(before)
                store = P.PolicyTransaction(fd)
                token = None
                if operation != 'begin':
                    token = store.begin('gpu')
                    if operation == 'rollback': store.accept(token, lambda: True)
                events = []
                store.checkpoint = events.append
                if operation == 'begin': store.begin('gpu')
                elif operation == 'accept': store.accept(token, lambda: True)
                else: store.rollback(token)
                counts[('present' if before else 'absent') + '-' + operation] = len(events)
                for boundary in range(1, len(events) + 1):
                    with self.subTest(before=before is not None, operation=operation, boundary=boundary, event=events[boundary - 1]):
                        self.crash_case(before, operation, boundary)
        self.assertEqual(sum(counts.values()), 53)
        print('PROCESS_DEATH_BOUNDARIES=' + json.dumps(counts, sort_keys=True), flush=True)

    def test_fsync_failure_recovers_without_auto_accepting(self):
        for failure in range(1, 8):
            root, fd = self.fixture(self.prior())
            real = os.fsync
            count = 0
            def flush(value):
                nonlocal count
                count += 1
                if count == failure: raise OSError('generated flush fault')
                return real(value)
            with patch.object(P.os, 'fsync', side_effect=flush):
                with self.assertRaises(OSError): P.PolicyTransaction(fd).begin('gpu')
            P.PolicyTransaction(fd).recover()
            self.assert_restored(root, self.prior())


if __name__ == '__main__': unittest.main()
