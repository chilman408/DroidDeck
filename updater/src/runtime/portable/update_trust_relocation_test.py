# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tested_trust_move', Path(__file__).with_name('update-trust-relocation.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
KEY = D.A.SPKI + bytes(range(32))  # PUBLIC fixture; never a signing seed.
PIN = D.P.sha(KEY)
RAW = D.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot', 'publicKeySpkiHex': KEY.hex()})


class Policy(unittest.TestCase):
    def test_public_trust_is_outside_original_closed_configuration_directory(self):
        self.assertEqual(D.A.TRUST, D.NEW)
        self.assertEqual(D.NEW.as_posix(), '/var/lib/droiddeck/update-trust.json')
        self.assertEqual(D.OLD.as_posix(), '/etc/droiddeck/update-trust.json')
        for args in ([], ['rotate', PIN], ['relocate-original', PIN, '--force']):
            with self.assertRaises(ValueError): D.main(args)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux public-only relocation fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='droiddeck-trust-move-fixture-', dir='/root')
        self.addCleanup(tmp.cleanup)
        parent = Path(tmp.name)
        for name in ('original', 'update'): (parent / name).mkdir(mode=0o700)
        self.old, self.new, self.root = parent / 'original/trust.json', parent / 'update/trust.json', parent / 'update/move'
        for name, value in (('OLD', self.old), ('NEW', self.new), ('ROOT', self.root)):
            item = patch.object(D, name, value); item.start(); self.addCleanup(item.stop)
        self.old.write_bytes(RAW); self.old.chmod(0o600)
        self.inode = self.old.stat().st_ino

    def test_move_preserves_original_inode_and_completed_inspection_is_readonly(self):
        result = D.relocate(PIN)
        self.assertFalse(result['keyEnrolled'])
        self.assertFalse(result['servicesChanged'])
        self.assertFalse(self.old.exists())
        self.assertEqual(self.new.read_bytes(), RAW)
        self.assertEqual(self.new.stat().st_ino, self.inode)
        before = {p.name: p.stat() for p in self.root.iterdir()}
        self.assertTrue(D.relocate(PIN)['readOnly'])
        self.assertEqual(before, {p.name: p.stat() for p in self.root.iterdir()})

    def test_actual_process_death_after_intent_and_after_move_resumes_same_original(self):
        for phase in ('after_intent', 'after_move'):
            child = os.fork()
            if child == 0:
                try: D.relocate(PIN, **{phase: lambda: os._exit(73)})
                except BaseException: os._exit(74)
                os._exit(75)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 73)
            self.assertFalse((self.root / 'complete.json').exists())
            D.relocate(PIN)
            self.assertEqual(self.new.stat().st_ino, self.inode)
            self.assertEqual(self.new.read_bytes(), RAW)
            if phase == 'after_intent':
                # Reset ONLY this generated fixture for the second kill boundary.
                for p in self.root.iterdir(): p.unlink()
                self.root.rmdir(); self.new.rename(self.old)

    def test_wrong_expected_key_refuses_before_move_or_journal(self):
        with self.assertRaises(ValueError): D.relocate('f' * 64)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.old.stat().st_ino, self.inode)

    def test_existing_destination_same_bytes_is_never_adopted_or_overwritten(self):
        self.new.write_bytes(RAW); self.new.chmod(0o600)
        before = self.new.stat()
        with self.assertRaises(ValueError): D.relocate(PIN)
        self.assertEqual(self.new.stat(), before)
        self.assertTrue(self.old.exists())

    def test_replaced_original_after_intent_refuses(self):
        def interrupt(): raise RuntimeError('fixture interruption')
        with self.assertRaises(RuntimeError): D.relocate(PIN, after_intent=interrupt)
        self.old.rename(self.old.with_name('original-retained'))
        self.old.write_bytes(RAW); self.old.chmod(0o600)
        with self.assertRaises(ValueError): D.relocate(PIN)
        self.assertFalse(self.new.exists())

    def test_unknown_or_copied_journal_refuses(self):
        def interrupt(): raise RuntimeError('fixture interruption')
        with self.assertRaises(RuntimeError): D.relocate(PIN, after_intent=interrupt)
        extra = self.root / 'unknown'; extra.write_bytes(b'fixture'); extra.chmod(0o600)
        with self.assertRaises(ValueError): D.relocate(PIN)
        self.assertTrue(self.old.exists())
        extra.unlink()
        retained = self.root.with_name('original-journal-retained')
        self.root.rename(retained)
        shutil.copytree(retained, self.root)
        with self.assertRaises(ValueError): D.relocate(PIN)
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())


if __name__ == '__main__':
    unittest.main()
