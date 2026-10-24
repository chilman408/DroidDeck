# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import runtime_registration_transaction_test as fixture

spec = importlib.util.spec_from_file_location('browser_install_tests', Path(__file__).with_name('runtime-browser-install.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class Boundary(unittest.TestCase):
    def test_only_independently_reviewed_manifest(self):
        for raw in (b'{}', b'private-value', b''):
            with self.assertRaises(ValueError):
                B.records(raw)
        self.assertEqual(B.PIN, 'fbca122a28633e3e9fb5c80653297dfed1c997bd17fe59b07f01b11691834f7d')


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        self.base = fixture.Native('runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.prepare()
        self.base.finish()
        self.root = self.base.root
        (self.root / 'var/lib/droiddeck').mkdir(parents=True)
        # The original fixture journal is moved once before the extension begins.
        Path(self.base.journal).rename(self.root / B.R.JOURNAL.lstrip('/'))
        self.payloads = {'bundle-manifest.json': b'fixture manifest', 'index.html': b'fixture page', 'css/main.css': b'fixture css'}
        self.held = {'check': lambda: None, 'files': tuple(sorted(self.payloads)), 'read': self.payloads.__getitem__}
        self.patch = mock.patch.object(B.R, '_inactive')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_install(self, mode='--install-new', **kwargs):
        return B.install(self.base.authority, self.held, mode=mode, prefix=str(self.root), **kwargs)

    def test_install_resume_and_verify_preserve_original_inodes(self):
        result = self.run_install()
        self.assertTrue(result['originalInstallationVerified'])
        self.assertFalse(result['servicesActivated'])
        self.assertEqual(self.run_install('--resume'), result)
        self.assertEqual(self.run_install('--verify'), result)
        for name, raw in self.payloads.items():
            self.assertEqual((self.root / B.TARGET.lstrip('/') / name).read_bytes(), raw)

    def test_death_after_intent_resumes_same_staged_inodes(self):
        child = os.fork()
        if child == 0:
            try:
                self.run_install(after_intent=lambda: os._exit(73))
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        with self.assertRaises(ValueError):
            self.run_install('--verify')
        self.assertTrue(self.run_install('--resume')['browserFilesInstalled'])

    def test_refuse_foreign_destination_or_replaced_browser_inode(self):
        target = self.root / B.TARGET.lstrip('/')
        target.mkdir()
        with self.assertRaises(Exception):
            self.run_install()
        target.rmdir()
        self.run_install()
        page = target / 'index.html'
        replacement = target / 'replacement'
        replacement.write_bytes(page.read_bytes())
        replacement.chmod(0o644)
        replacement.replace(page)
        with self.assertRaises(ValueError):
            self.run_install('--resume')

    def test_no_new_install_or_extra_files_adopted(self):
        self.run_install()
        with self.assertRaises(Exception):
            self.run_install()
        (self.root / B.TARGET.lstrip('/') / 'foreign').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.run_install('--verify')


if __name__ == '__main__':
    unittest.main()
