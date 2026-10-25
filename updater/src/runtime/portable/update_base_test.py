# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('update_base_tests', Path(__file__).with_name('update-base.py'))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)


class Boundary(unittest.TestCase):
    def test_canonical_receipts_only(self):
        self.assertEqual(U.canonical(b'{"a":1}\n'), {'a': 1})
        for raw in (b'{ "a":1}\n', b'{"a":1,"a":1}\n', b'{"a":1}', b'[] junk'):
            with self.assertRaises((ValueError, UnicodeError)):
                U.canonical(raw)

    def test_all_pinned_rc2_phases(self):
        names = U.phase_names(Path(__file__).with_name('install-machine.py').read_bytes())
        self.assertEqual(len(names), 35)
        self.assertIn('03a-gpu-preflight', names)
        self.assertEqual(names[-1], '34-verify-gpu')

    def test_missing_duplicate_dynamic_phase_refused(self):
        for raw in (b'phase("01",f)', b'\n'.join([b'phase("01",f)'] * 35), b'phase(name,f)'):
            with self.assertRaises(ValueError):
                U.phase_names(raw)

    def records(self):
        config = {'user': 'generated'}
        intent = {'schema': 1, 'configuration': config, 'packageManifestSha256': U.PACKAGE_PIN,
                  'machineSha256': 'a' * 64}
        complete = {'kind': 'clean-machine-installation', 'transaction': 'a' * 12,
                    'browserRebootRecoveryAcceptanceRequired': True, 'installable': False}
        return intent, complete, config

    def test_completed_baseline(self):
        intent, complete, config = self.records()
        U.completed(intent, complete, config, 'a' * 64)

    def test_foreign_machine_package_configuration_refused(self):
        intent, complete, config = self.records()
        for field, value in (('machineSha256', 'b' * 64), ('packageManifestSha256', 'b' * 64),
                             ('configuration', {'user': 'foreign'}), ('schema', True), ('extra', True)):
            with self.assertRaises(ValueError):
                U.completed({**intent, field: value}, complete, config, 'a' * 64)

    def test_incomplete_or_permissive_completion_refused(self):
        intent, complete, config = self.records()
        for field, value in (('transaction', '../other'), ('installable', True), ('extra', False),
                             ('browserRebootRecoveryAcceptanceRequired', 1), ('kind', 'partial')):
            with self.assertRaises(ValueError):
                U.completed(intent, {**complete, field: value}, config, 'a' * 64)

    def test_no_mutating_cli_modes(self):
        for args in ([], ['apply', '/root/source'], ['inspect'], ['resume', '/root/source']):
            with self.assertRaises(ValueError):
                U.main(args)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        import runtime_compat_install_test as fixture
        self.fixture = fixture.Native('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install()
        self.root = self.fixture.base.root
        for name, value in (('C', fixture.C), ('B', fixture.C.B), ('R', fixture.C.R),
                            ('COMPONENTS', {'display': '/opt/droiddeck/runtime',
                                            'frontend': '/opt/droiddeck/frontend'})):
            patch = mock.patch.object(U, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def inspect(self, **kwargs):
        return U.owned_inventory(self.fixture.base.base.authority, self.fixture.base.held,
                                 self.fixture.source, prefix=str(self.root), **kwargs)

    def replace(self, target):
        temporary = target.with_name('replacement')
        temporary.write_bytes(target.read_bytes())
        temporary.chmod(target.stat().st_mode & 0o777)
        temporary.replace(target)

    def test_original_inventory_readonly(self):
        before = {str(path): (path.lstat().st_ino, path.lstat().st_mtime_ns)
                  for path in self.root.rglob('*')}
        result = self.inspect()
        self.assertEqual([row['name'] for row in result['components']], ['display', 'frontend'])
        self.assertEqual(before, {str(path): (path.lstat().st_ino, path.lstat().st_mtime_ns)
                                 for path in self.root.rglob('*')})
        files = result['components'][0]['files']
        self.assertEqual(next(row for row in files if row['path'] == 'f2')['sha256'], U.C.PIN)

    def test_same_bytes_foreign_compat_refused(self):
        self.replace(self.root / U.C.TARGET.lstrip('/'))
        with self.assertRaises(ValueError):
            self.inspect()

    def test_same_bytes_foreign_frontend_refused(self):
        self.replace(self.root / 'opt/droiddeck/frontend/index.html')
        with self.assertRaises(ValueError):
            self.inspect()

    def test_unknown_file_refused(self):
        (self.root / 'opt/droiddeck/frontend/foreign').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.inspect()

    def test_displaced_original_replacement_refused(self):
        self.replace(self.root / U.C.JOURNAL.lstrip('/') / 'payload')
        with self.assertRaises(ValueError):
            self.inspect()

    def test_concurrent_same_byte_replacement_refused(self):
        calls = []

        def guard():
            calls.append(True)
            if len(calls) == 2:
                self.replace(self.root / 'opt/droiddeck/frontend/index.html')

        with self.assertRaises(ValueError):
            self.inspect(guard=guard)

    def test_protected_record_generation_bound(self):
        from contextlib import ExitStack
        path = self.root / 'receipt.json'
        path.write_bytes(b'{"generated":true}\n')
        path.chmod(0o600)
        with ExitStack() as stack:
            records = U.Records(stack)
            records.read(path)
            records.check()
            self.replace(path)
            with self.assertRaises(ValueError):
                records.check()

    def test_protected_record_symlink_and_hardlink_refused(self):
        from contextlib import ExitStack
        path = self.root / 'receipt.json'
        path.write_bytes(b'generated')
        path.chmod(0o600)
        link = self.root / 'link'
        link.symlink_to(path)
        with ExitStack() as stack:
            with self.assertRaises((OSError, ValueError)):
                U.Records(stack).read(link)
        link.unlink()
        os.link(path, link)
        with ExitStack() as stack:
            with self.assertRaises(ValueError):
                U.Records(stack).read(path)

    def test_special_file_and_oversized_record_refused(self):
        from contextlib import ExitStack
        path = self.root / 'receipt.json'
        os.mkfifo(path, mode=0o600)
        with ExitStack() as stack:
            with self.assertRaises(ValueError):
                U.Records(stack).read(path)
        path.unlink()
        path.write_bytes(b'generated')
        path.chmod(0o600)
        with ExitStack() as stack:
            with self.assertRaises(ValueError):
                U.Records(stack).read(path, maximum=2)


if __name__ == '__main__':
    unittest.main()
