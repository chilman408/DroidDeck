# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('exchange_tested', Path(__file__).with_name('update-exchange.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def fixture():
    temporary = tempfile.TemporaryDirectory(prefix='droiddeck-update-exchange-fixture-', dir='/root')
    root, plan = Path(temporary.name), []
    for name in ('installed', 'prepared'):
        (root / name).mkdir(mode=0o700)
    for role in D.ROLES:
        row = {'name': role}
        for name, side in (('installed', 'before'), ('prepared', 'after')):
            component = root / name / role
            component.mkdir(mode=0o755)
            (component / 'node_modules').mkdir(mode=0o755)
            (component / 'node_modules/demo').mkdir(mode=0o755)
            rows = []
            for file in ('index.js', 'node_modules/demo/index.js'):
                raw = ('// generated fixture ' + side + role + file + '\n').encode()
                path = component / file
                path.write_bytes(raw); path.chmod(0o644)
                rows.append({'path': file, 'size': len(raw), 'sha256': D.P.sha(raw), 'mode': 0o644})
            row[side] = rows
        plan.append(row)
    return temporary, root, plan


def identities(root):
    return {role: (root / 'installed' / role).stat().st_ino for role in D.ROLES}


class Policy(unittest.TestCase):
    def test_fixed_sorted_components_and_real_code_change(self):
        rows = [{'path': 'index.js', 'size': 1, 'sha256': 'a' * 64, 'mode': 0o644}]
        plan = [{'name': role, 'before': rows, 'after': [{**rows[0], 'sha256': 'b' * 64}]} for role in D.ROLES]
        self.assertEqual(tuple(D.components(plan)), D.ROLES)
        for value in ([], plan[::-1], [{**plan[0], 'extra': True}, *plan[1:]],
                      [{**row, 'after': row['before']} for row in plan]):
            with self.assertRaises(ValueError): D.components(value)

    def test_no_live_apply_cli(self):
        result = subprocess.run([sys.executable, '-I', '-B', D.__file__, 'apply'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'No live apply', result.stderr)


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux private exchange/interruption fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        temporary, self.root, self.plan = fixture()
        self.addCleanup(temporary.cleanup)
        self.before = identities(self.root)

    def prepare(self):
        return D.prepare(self.root, self.plan)

    def test_apply_rollback_and_reopen_preserve_original_directory_inodes(self):
        self.prepare()
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            self.assertEqual(set(transaction.apply(lambda: None)['orientation'].values()), {'after'})
        self.assertNotEqual(identities(self.root), self.before)
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            result = transaction.rollback(lambda: None)
            self.assertTrue(result['rollbackComplete']); self.assertFalse(result['livePublicationPerformed'])
            self.assertFalse(result['installationAuthorized']); self.assertFalse(result['readyForApply'])
        self.assertEqual(identities(self.root), self.before)
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            original = {file.name: file.stat() for file in (self.root / 'journal').iterdir()}
            self.assertTrue(transaction.rollback(lambda: self.fail('No mutation guard needed for completed read-only result'))['rollbackComplete'])
            self.assertEqual(original, {file.name: file.stat() for file in (self.root / 'journal').iterdir()})
            with self.assertRaises(ValueError): transaction.apply(lambda: None)

    def test_foreign_same_byte_file_or_directory_never_adopted(self):
        self.prepare()
        target = self.root / 'installed/display/index.js'
        raw = target.read_bytes(); target.unlink(); target.write_bytes(raw); target.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, self.root)
        self.assertEqual(identities(self.root), self.before)

    def test_unknown_objects_and_changed_permissions_refuse_before_exchange(self):
        self.prepare()
        extra = self.root / 'prepared/frontend/extra.js'
        extra.write_bytes(b'unknown'); extra.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, self.root)
        extra.unlink()
        (self.root / 'prepared/frontend/index.js').chmod(0o600)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, self.root)
        self.assertEqual(identities(self.root), self.before)

    def test_linked_input_and_existing_partial_preparation_refuse(self):
        target = self.root / 'prepared/frontend/index.js'
        target.unlink(); target.symlink_to(self.root / 'prepared/display/index.js')
        with self.assertRaises((OSError, ValueError)): self.prepare()
        self.assertFalse((self.root / 'journal').exists())
        target.unlink(); os.link(self.root / 'prepared/display/index.js', target)
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse((self.root / 'journal').exists())

    def test_copied_journal_and_missing_completion_cannot_be_adopted(self):
        self.prepare()
        copied = tempfile.TemporaryDirectory(prefix='droiddeck-update-exchange-fixture-copy-', dir='/root')
        self.addCleanup(copied.cleanup)
        shutil.copytree(self.root, copied.name, dirs_exist_ok=True)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, copied.name)
        ready = self.root / 'journal/ready.json'
        ready.unlink()
        with ExitStack() as stack, self.assertRaises(FileNotFoundError): D.Transaction(stack, self.root)
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(identities(self.root), self.before)

    def test_reordered_or_unknown_phase_never_authorizes_exchange(self):
        self.prepare()
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            fd = transaction.parents['journal']
            D.write(fd, 'apply-intent-frontend.json', {'schema': 1, 'originalSha256': transaction.pin, 'phase': 'apply-intent-frontend'})
            with self.assertRaises(ValueError): transaction.observe()
        self.assertEqual(identities(self.root), self.before)
        unknown = self.root / 'journal/unknown.json'
        unknown.write_bytes(b'{}\n'); unknown.chmod(0o600)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, self.root)

    def test_real_death_during_preparation_leaves_original_code_and_no_false_completion(self):
        code = '''import importlib.util,json,os,sys
s=importlib.util.spec_from_file_location('prepare_child',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
write=d.write
def interrupted(parent,name,value):
    write(parent,name,value)
    if name=='original.json':os._exit(73)
d.write=interrupted
d.prepare(sys.argv[2],json.loads(sys.argv[3]))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(self.root), D.P.encode(self.plan).decode()],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        self.assertEqual(set(path.name for path in (self.root / 'journal').iterdir()), {'original.json'})
        self.assertEqual(identities(self.root), self.before)
        with ExitStack() as stack, self.assertRaises(FileNotFoundError): D.Transaction(stack, self.root)
        with self.assertRaises(ValueError): self.prepare()

    def test_guard_refusal_has_no_exchange_and_late_refusal_has_explicit_rollback(self):
        self.prepare()
        def fail(): raise ValueError('generated guard refusal')
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            with self.assertRaises(ValueError): transaction.apply(fail)
            self.assertEqual(identities(self.root), self.before)
            def checkpoint(phase, role):
                if (phase, role) == ('exchanged', 'display'): raise RuntimeError('generated interruption')
            with self.assertRaises(RuntimeError): transaction.apply(lambda: None, checkpoint=checkpoint)
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            with self.assertRaises(ValueError): transaction.apply(lambda: None)
            self.assertTrue(transaction.rollback(lambda: None)['rollbackComplete'])
        self.assertEqual(identities(self.root), self.before)

    def test_inflight_file_substitution_refuses_without_overwriting_foreign_bytes(self):
        self.prepare()
        target = self.root / 'prepared/display/index.js'
        def checkpoint(phase, role):
            if (phase, role) == ('intent', 'display'):
                target.write_bytes(b'foreign'); target.chmod(0o644)
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            with self.assertRaises(ValueError): transaction.apply(lambda: None, checkpoint=checkpoint)
        self.assertEqual(target.read_bytes(), b'foreign')
        self.assertEqual(identities(self.root), self.before)

    def test_journal_and_role_parent_replacements_refuse(self):
        self.prepare()
        with ExitStack() as stack:
            transaction = D.Transaction(stack, self.root)
            ready = self.root / 'journal/ready.json'
            raw = ready.read_bytes(); ready.unlink(); ready.write_bytes(raw); ready.chmod(0o600)
            with self.assertRaises(ValueError): transaction.observe()
        foreign = self.root / 'foreign'
        (self.root / 'prepared').rename(foreign)
        (self.root / 'prepared').mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError): D.Transaction(stack, self.root)

    def test_concurrent_controller_cannot_acquire_original_transaction(self):
        self.prepare()
        with ExitStack() as stack:
            D.Transaction(stack, self.root)
            with ExitStack() as other, self.assertRaises(BlockingIOError): D.Transaction(other, self.root)

    def child(self, root, operation, phase, role):
        code = '''import importlib.util,os,sys
from contextlib import ExitStack
s=importlib.util.spec_from_file_location('child_exchange',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
def checkpoint(phase,role):
    if (phase,role)==(sys.argv[4],sys.argv[5]):os._exit(73)
with ExitStack() as stack:
    value=d.Transaction(stack,sys.argv[2])
    getattr(value,sys.argv[3])(lambda:None,checkpoint=checkpoint)
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(root), operation, phase, role],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode())

    def test_real_process_death_before_and_after_each_apply_exchange_rolls_back(self):
        for phase in ('intent', 'exchanged', 'done'):
            for role in D.ROLES:
                with self.subTest(phase=phase, role=role):
                    temporary, root, plan = fixture()
                    with temporary:
                        before = identities(root); D.prepare(root, plan)
                        self.child(root, 'apply', phase, role)
                        with ExitStack() as stack:
                            transaction = D.Transaction(stack, root)
                            with self.assertRaises(ValueError): transaction.apply(lambda: None)
                            self.assertTrue(transaction.rollback(lambda: None)['rollbackComplete'])
                        self.assertEqual(identities(root), before)

    def test_real_process_death_before_and_after_each_restore_resumes_only_original_rollback(self):
        for phase in ('restore-intent', 'restored'):
            for role in D.ROLES:
                with self.subTest(phase=phase, role=role):
                    temporary, root, plan = fixture()
                    with temporary:
                        before = identities(root); D.prepare(root, plan)
                        with ExitStack() as stack: D.Transaction(stack, root).apply(lambda: None)
                        self.child(root, 'rollback', phase, role)
                        with ExitStack() as stack:
                            self.assertTrue(D.Transaction(stack, root).rollback(lambda: None)['rollbackComplete'])
                        self.assertEqual(identities(root), before)


if __name__ == '__main__':
    unittest.main()
