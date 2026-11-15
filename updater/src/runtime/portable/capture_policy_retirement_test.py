# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('policy_transaction', Path(__file__).with_name('capture-policy-transaction.py'))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
NATIVE = sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0


class InputTests(unittest.TestCase):
    def test_retirement_rejects_bad_token_or_missing_proof_before_storage(self):
        store = object.__new__(P.PolicyTransaction)
        for token, proof in ((None, lambda: True), ('../private', lambda: True), ('a' * 32, None), ('A' * 32, lambda: True)):
            with self.assertRaisesRegex(ValueError, 'Exact terminal'): store.retire(token, proof)


@unittest.skipUnless(NATIVE, 'Root Linux receipt/rename/flock tests')
class NativeTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(prefix='generated-policy-retire-'); self.addCleanup(directory.cleanup)
        root = Path(directory.name); root.chmod(0o700)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); self.addCleanup(os.close, fd)
        return root, fd, P.PolicyTransaction(fd)

    def finish(self, store, token, accepted):
        if accepted: store.accept(token, lambda: True)
        else: store.rollback(token)

    def test_repeated_cpu_gpu_cycles_preserve_all_terminal_receipts(self):
        root, fd, store = self.fixture()
        receipts = []
        for index in range(6):
            mode = 'gpu' if index % 2 == 0 else 'cpu'
            token = store.begin(mode)
            store.accept(token, lambda: True)
            raw = (root / P.JOURNAL).read_bytes(); inode = (root / P.JOURNAL).stat().st_ino
            result = store.retire(token, lambda: True)
            file = root / result['receipt']; receipts.append((file, raw, inode))
            self.assertEqual(file.read_bytes(), raw); self.assertEqual(file.stat().st_ino, inode)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertFalse(os.path.lexists(root / P.JOURNAL))
            self.assertEqual(P.parse_policy((root / P.POLICY).read_bytes()), mode)
            self.assertEqual(P.PolicyTransaction(fd).recover(), 'no-transaction')
        for file, raw, inode in receipts:
            self.assertEqual((file.read_bytes(), file.stat().st_ino), (raw, inode))

    def test_rolled_back_absence_retirement_allows_fresh_toggle(self):
        root, fd, store = self.fixture(); token = store.begin('gpu'); store.rollback(token)
        result = store.retire(token, lambda: True)
        self.assertEqual(result['phase'], 'rolled-back'); self.assertFalse(os.path.lexists(root / P.POLICY))
        next_token = store.begin('gpu'); store.rollback(next_token)
        with self.assertRaises(ValueError): store.retire(token, lambda: True)
        self.assertEqual(store.inspect()['token'], next_token)

    def test_pending_or_false_health_never_archives(self):
        root, fd, store = self.fixture(); token = store.begin('gpu')
        with self.assertRaises(ValueError): store.retire(token, lambda: True)
        store.accept(token, lambda: True)
        before = (root / P.JOURNAL).read_bytes()
        for proof in (lambda: False, lambda: 1):
            with self.assertRaises(ValueError): store.retire(token, proof)
            self.assertEqual((root / P.JOURNAL).read_bytes(), before)
        store.rollback(token)
        with self.assertRaisesRegex(ValueError, 'reentry'): store.retire(token, store.recover)

    def test_foreign_policy_or_journal_mutation_during_proof_preserved(self):
        for target in ('policy', 'journal'):
            root, fd, store = self.fixture(); token = store.begin('gpu'); store.accept(token, lambda: True)
            file = root / (P.POLICY if target == 'policy' else P.JOURNAL)
            def interfere():
                file.rename(root / 'generated-prior')
                file.write_bytes(b'generated-private-sentinel'); file.chmod(0o600)
                return True
            with self.assertRaises(ValueError): store.retire(token, interfere)
            self.assertEqual(file.read_bytes(), b'generated-private-sentinel')
            self.assertFalse(os.path.lexists(root / ('.capture-policy-history-' + token + '.json')))

    def test_foreign_existing_receipt_or_symlink_never_replaced(self):
        for link in (False, True):
            root, fd, store = self.fixture(); token = store.begin('gpu'); store.rollback(token)
            file = root / ('.capture-policy-history-' + token + '.json')
            if link: file.symlink_to('generated-missing')
            else: file.write_bytes(b'generated-private-receipt'); file.chmod(0o600)
            with self.assertRaises((ValueError, OSError)): store.retire(token, lambda: True)
            self.assertTrue(os.path.lexists(root / P.JOURNAL))
            if link: self.assertEqual(os.readlink(file), 'generated-missing')
            else: self.assertEqual(file.read_bytes(), b'generated-private-receipt')

    def test_token_reuse_is_rejected_before_new_journal_or_policy(self):
        for kind in ('history', 'next', 'restore'):
            root, fd, store = self.fixture(); token = 'a' * 32
            name = '.capture-policy-history-' + token + '.json' if kind == 'history' else '.capture-policy-' + kind + '-' + token
            (root / name).symlink_to('generated-missing')
            with patch.object(P.secrets, 'token_hex', return_value=token):
                with self.assertRaisesRegex(ValueError, 'retained state'): store.begin('gpu')
            self.assertFalse(os.path.lexists(root / P.JOURNAL)); self.assertFalse(os.path.lexists(root / P.POLICY))

    def test_unattributed_scratch_is_retained_and_not_replay_authority(self):
        root, fd, store = self.fixture(); scratch = root / ('.capture-policy-write-' + 'b' * 32)
        scratch.write_bytes(b'generated-private-scratch'); scratch.chmod(0o600)
        inode = scratch.stat().st_ino
        token = store.begin('gpu'); store.rollback(token); store.retire(token, lambda: True)
        self.assertEqual((scratch.read_bytes(), scratch.stat().st_ino), (b'generated-private-scratch', inode))
        self.assertEqual(store.recover(), 'no-transaction')
        with self.assertRaises(ValueError): store.rollback(token)

    def test_process_death_at_retirement_boundaries_is_idempotently_recoverable(self):
        for accepted in (False, True):
            for boundary in (1, 2):
                root, fd, store = self.fixture(); token = store.begin('gpu'); self.finish(store, token, accepted)
                raw = (root / P.JOURNAL).read_bytes(); inode = (root / P.JOURNAL).stat().st_ino
                pid = os.fork()
                if pid == 0:
                    count = 0
                    def crash(event):
                        nonlocal count
                        count += 1
                        if count == boundary: os._exit(77)
                    try: P.PolicyTransaction(fd, checkpoint=crash).retire(token, lambda: True); os._exit(99)
                    except BaseException: os._exit(98)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 77)
                result = P.PolicyTransaction(fd).retire(token, lambda: True)
                file = root / result['receipt']
                self.assertEqual((file.read_bytes(), file.stat().st_ino), (raw, inode))
                self.assertFalse(os.path.lexists(root / P.JOURNAL))
                self.assertEqual(store.recover(), 'no-transaction')
        print('RETIREMENT_PROCESS_DEATH_BOUNDARIES=4', flush=True)

    def test_directory_flush_failure_after_archive_is_retryable(self):
        root, fd, store = self.fixture(); token = store.begin('gpu'); store.rollback(token)
        with patch.object(P.os, 'fsync', side_effect=OSError('generated flush fault')):
            with self.assertRaises(OSError): store.retire(token, lambda: True)
        result = P.PolicyTransaction(fd).retire(token, lambda: True)
        self.assertTrue(result['retired']); self.assertFalse(os.path.lexists(root / P.POLICY))


if __name__ == '__main__': unittest.main()
