# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
import runtime_browser_install_test as fixture

spec = importlib.util.spec_from_file_location('offline_runtime_tested', Path(__file__).with_name('install-runtime.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_closed_modes_and_redacted_errors(self):
        with mock.patch.object(D.R.A, '_root_linux'):
            for args in ([], ['--force'], ['--install-runtime', 'u', 'a', 'r', 'f', 's', 'private']):
                with self.assertRaises(ValueError):
                    D.main(args)
        result = subprocess.run([sys.executable, '-I', '-B', D.__file__, '--force', 'private-value'],
                                capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertNotIn(b'private-value', result.stderr)

    def test_invalid_operation_never_queries_or_mutates(self):
        with mock.patch.object(D.R, '_inactive') as active, self.assertRaises(ValueError):
            D.finish({}, {}, '/unused', mode='--force')
        active.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated offline fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        self.base = fixture.Native('runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root = self.base.root
        self.source = self.root / 'compat-source'
        self.source.write_bytes(b'generated compatibility source')
        self.source.chmod(0o600)
        for obj, name, value in ((D.C, 'TARGET', '/opt/droiddeck/runtime/f2'),
                                 (D.C, 'PIN', D.R._sha(self.source.read_bytes()))):
            patch = mock.patch.object(obj, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(D.R, '_inactive')
        patch.start()
        self.addCleanup(patch.stop)

    def finish(self, mode='--install-runtime', **kwargs):
        return D.finish(self.base.base.authority, self.base.held, self.source,
                        mode=mode, prefix=str(self.root), **kwargs)

    def identities(self):
        names = (*D.R.K.REGISTRATIONS.values(), *D.S.FILES, *D.S.RESET_FILES,
                 D.C.TARGET, D.B.TARGET + '/index.html')
        return {name: Path(D.I.mapped(str(self.root), name)).stat().st_ino for name in names}

    def test_complete_resume_and_readonly_verify_without_replaying_base(self):
        result = self.finish()
        self.assertTrue(result['runtimeOwnershipVerified'])
        self.assertEqual(result['registeredFilesVerified'], 12)
        self.assertFalse(result['servicesActivated'])
        before = self.identities()
        with mock.patch.object(D.I.R, '_finish', side_effect=AssertionError('base replay')), \
                mock.patch.object(D.B, 'install', side_effect=AssertionError('browser replay')):
            self.assertTrue(self.finish('--resume-runtime')['runtimeOwnershipVerified'])
            with mock.patch.object(D.R, '_inactive', side_effect=AssertionError('readonly inactive query')), \
                    mock.patch.object(D.S.os, 'fsync', side_effect=AssertionError('readonly fsync')):
                self.assertTrue(self.finish('--verify-runtime')['readOnly'])
        self.assertEqual(self.identities(), before)

    def die_and_resume(self, phase):
        child = os.fork()
        if child == 0:
            try:
                self.finish(after_phase=lambda name: os._exit(73) if name == phase else None)
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertTrue(self.finish('--resume-runtime')['runtimeOwnershipVerified'])
        self.assertTrue(self.finish('--verify-runtime')['readOnly'])

    def test_real_death_after_registration(self):
        self.die_and_resume('registration')

    def test_real_death_after_browser(self):
        self.die_and_resume('browser')

    def test_real_death_after_compatibility(self):
        self.die_and_resume('compatibility')

    def pending_and_resume(self, module):
        child = os.fork()
        if child == 0:
            original = module.install
            def pending(*args, **kwargs):
                return original(*args, **kwargs, after_intent=lambda: os._exit(73))
            try:
                with mock.patch.object(module, 'install', side_effect=pending):
                    self.finish()
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertTrue(self.finish('--resume-runtime')['runtimeOwnershipVerified'])

    def test_real_death_before_browser_publication(self):
        self.pending_and_resume(D.B)

    def test_real_death_before_compatibility_exchange(self):
        self.pending_and_resume(D.C)

    def test_foreign_recovery_or_same_byte_compatibility_object_refuses(self):
        self.finish()
        target = Path(D.I.mapped(str(self.root), D.C.TARGET))
        replacement = self.root / 'foreign'
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o644)
        replacement.replace(target)
        with self.assertRaises(ValueError):
            self.finish('--verify-runtime')
        with self.assertRaises(ValueError):
            self.finish('--resume-runtime')

    def test_empty_browser_journal_is_not_adopted(self):
        Path(D.I.mapped(str(self.root), D.B.JOURNAL)).mkdir(mode=0o700)
        with self.assertRaises(Exception):
            self.finish('--resume-runtime')
        self.assertFalse(Path(D.I.mapped(str(self.root), D.C.JOURNAL)).exists())

    def test_readonly_incomplete_and_active_mutation_refuse(self):
        with self.assertRaises(ValueError):
            self.finish('--verify-runtime')
        with mock.patch.object(D.R, '_inactive', side_effect=ValueError('active')), self.assertRaises(ValueError):
            self.finish()
        self.assertFalse(Path(D.I.mapped(str(self.root), D.B.JOURNAL)).exists())

    def test_pending_recovery_verification_does_not_publish(self):
        authority = self.base.base.authority
        journal = D.I.mapped(str(self.root), D.S.JOURNAL)
        D.S.prepare(authority, journal, str(self.root), guard=lambda: None)
        with mock.patch.object(D.S.R.J, '_rename_new', side_effect=AssertionError('verify published')), \
                mock.patch.object(D.S.os, 'fsync', side_effect=AssertionError('verify synced')), self.assertRaises(ValueError):
            D.S.finish(authority, journal, str(self.root), guard=lambda: None, verify_only=True)
        self.assertFalse(any(Path(D.I.mapped(str(self.root), name)).exists() for name in D.S.FILES))


if __name__ == '__main__':
    unittest.main()
