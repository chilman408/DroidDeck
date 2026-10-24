# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import runtime_browser_install_test as fixture

spec = importlib.util.spec_from_file_location('compat_install_tests', Path(__file__).with_name('runtime-compat-install.py'))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        self.base = fixture.Native('runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.run_install()
        self.source = self.base.root / 'source'
        self.source.write_bytes(b'generated compatibility payload')
        self.source.chmod(0o600)
        self.old = (self.base.root / 'opt/droiddeck/runtime/f2').stat()
        for name, value in [('TARGET', '/opt/droiddeck/runtime/f2'), ('PIN', C.R._sha(self.source.read_bytes()))]:
            patch = mock.patch.object(C, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(C.R, '_inactive')
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, mode='--install-new', **kwargs):
        return C.install(self.base.base.authority, self.base.held, self.source, mode=mode,
            prefix=str(self.base.root), **kwargs)

    def test_success_resume_verify_and_original_displaced_inode(self):
        result = self.install()
        self.assertTrue(result['compatibilityInstalled'])
        self.assertFalse(result['servicesActivated'])
        self.assertEqual(self.install('--resume'), result)
        self.assertEqual(self.install('--verify'), result)
        old = self.base.root / C.JOURNAL.lstrip('/') / 'payload'
        self.assertEqual((old.stat().st_dev, old.stat().st_ino), (self.old.st_dev, self.old.st_ino))

    def test_process_death_before_exchange_resumes(self):
        child = os.fork()
        if child == 0:
            try:
                self.install(after_intent=lambda: os._exit(73))
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        with self.assertRaises(ValueError):
            self.install('--verify')
        self.assertTrue(self.install('--resume')['compatibilityInstalled'])

    def test_foreign_same_byte_replacement_refused(self):
        self.install()
        target = self.base.root / C.TARGET.lstrip('/')
        other = target.with_name('foreign')
        other.write_bytes(target.read_bytes())
        other.chmod(0o644)
        other.replace(target)
        with self.assertRaises(ValueError):
            self.install('--resume')


if __name__ == '__main__':
    unittest.main()
