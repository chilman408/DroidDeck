# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hotfix_tested', Path(__file__).with_name('reboot-hotfix.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


@unittest.skipUnless(os.geteuid() == 0, 'Root Linux isolated fixtures required')
class Update(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root')
        self.root = Path(self.temp.name)
        self.target = self.root / 'fresh-gpu.py'
        self.journal = self.root / 'journal'
        self.old, self.new = b'generated original source', b'generated tested replacement'
        self.target.write_bytes(self.old)
        self.target.chmod(0o600)
        self.pins = [patch.object(H, 'OLD', hashlib.sha256(self.old).hexdigest()),
                     patch.object(H, 'NEW', hashlib.sha256(self.new).hexdigest())]
        for value in self.pins:
            value.start()

    def tearDown(self):
        for value in self.pins:
            value.stop()
        self.temp.cleanup()

    def apply(self, **kwargs):
        return H.apply(self.new, target=self.target, journal=self.journal, **kwargs)

    def test_update_and_repeated_explicit_resume_preserve_original_backup(self):
        self.apply()
        info = self.target.stat()
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual((self.journal / 'original.py').read_bytes(), self.old)
        self.apply(resume=True)
        self.assertEqual(self.target.stat().st_ino, info.st_ino)
        with self.assertRaises(ValueError):
            self.apply()

    def test_interruption_after_replace_is_durably_resumed_without_replacement(self):
        def interrupted():
            raise RuntimeError('generated interruption')
        with self.assertRaises(RuntimeError):
            self.apply(after_replace=interrupted)
        info = self.target.stat()
        self.apply(resume=True)
        self.assertEqual(self.target.stat().st_ino, info.st_ino)

    def test_foreign_payload_target_and_backup_refuse(self):
        with self.assertRaises(ValueError):
            H.apply(b'foreign', target=self.target, journal=self.journal)
        self.target.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.journal.exists())
        self.target.write_bytes(self.old)
        self.apply()
        (self.journal / 'original.py').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.apply(resume=True)

    def test_symlink_and_hardlink_targets_are_not_followed(self):
        other = self.root / 'other.py'
        self.target.rename(other)
        self.target.symlink_to(other)
        with self.assertRaises(OSError):
            self.apply()
        self.target.unlink()
        os.link(other, self.target)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(other.read_bytes(), self.old)

    def test_deadline_publication_and_resume_require_original_inode(self):
        self.journal.mkdir(mode=0o700)
        output = self.root / 'session.d'
        output.mkdir(mode=0o755)
        target = output / '90-startup.conf'
        raw = b'[Service]\nTimeoutStartSec=180s\n'
        H.publish_deadline(target, raw, self.journal)
        inode = target.stat().st_ino
        H.publish_deadline(target, raw, self.journal)
        self.assertEqual(target.stat().st_ino, inode)
        foreign = output / 'foreign'
        foreign.write_bytes(raw)
        foreign.chmod(0o600)
        foreign.replace(target)
        with self.assertRaises(ValueError):
            H.publish_deadline(target, raw, self.journal)


if __name__ == '__main__':
    unittest.main()
