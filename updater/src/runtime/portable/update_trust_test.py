# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trust_tested', Path(__file__).with_name('update-trust.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
KEY = T.A.SPKI + bytes(range(32))  # Public fixture only; no signing seed/key.
PIN = T.P.sha(KEY)
RAW = T.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot', 'publicKeySpkiHex': KEY.hex()})


class Policy(unittest.TestCase):
    def test_exact_public_fingerprint(self):
        self.assertEqual(T.checked(RAW, PIN), KEY)
        for pin in ('f' * 64, PIN.upper(), '', True):
            with self.assertRaises(ValueError): T.checked(RAW, pin)

    def test_no_bundle_key_private_material_or_noncanonical_trust(self):
        value = T.P.document(RAW)
        for raw in (RAW[:-1], RAW + b' ', T.P.encode({**value, 'channel': 'stable'}),
                    T.P.encode({**value, 'privateKey': 'refused'}),
                    T.P.encode({**value, 'publicKeySpkiHex': '00' * 48})):
            with self.assertRaises(ValueError): T.checked(raw, PIN)

    def test_no_force_rotation_or_automatic_mode(self):
        with patch.object(T, 'enroll') as enroll, patch.object(T, 'inspect') as inspect:
            for args in ([], ['enroll'], ['rotate', '/public', PIN], ['enroll', '/public', PIN, '--force']):
                with self.assertRaises(ValueError): T.main(args)
            enroll.assert_not_called(); inspect.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux protected public enrollment fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='droiddeck-trust-test-', dir='/root')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'public.json'
        self.source.write_bytes(RAW); self.source.chmod(0o600)
        p = patch.object(T.A, 'TRUST', self.root / 'trust.json')
        p.start(); self.addCleanup(p.stop)

    def test_exclusive_enrollment_and_readonly_inspection(self):
        result = T.enroll(self.source, PIN)
        self.assertTrue(result['newPublicKeyEnrolled'])
        self.assertFalse(result['servicesChanged']); self.assertFalse(result['readyForApply'])
        before = T.A.TRUST.stat()
        self.assertEqual(before.st_mode & 0o777, 0o600)
        self.assertTrue(T.inspect(PIN)['readOnly'])
        self.assertEqual(before, T.A.TRUST.stat())
        with self.assertRaises(FileExistsError): T.enroll(self.source, PIN)
        self.assertEqual(before, T.A.TRUST.stat())

    def test_wrong_fingerprint_cannot_create_trust(self):
        with self.assertRaises(ValueError): T.enroll(self.source, 'f' * 64)
        self.assertFalse(T.A.TRUST.exists())

    def test_existing_partial_link_and_unknown_trust_never_adopted(self):
        T.A.TRUST.write_bytes(b'partial'); T.A.TRUST.chmod(0o600)
        before = T.A.TRUST.stat()
        with self.assertRaises(FileExistsError): T.enroll(self.source, PIN)
        with self.assertRaises(ValueError): T.inspect(PIN)
        self.assertEqual(before, T.A.TRUST.stat())
        T.A.TRUST.unlink(); T.A.TRUST.symlink_to(self.source)
        with self.assertRaises(OSError): T.enroll(self.source, PIN)
        with self.assertRaises(OSError): T.inspect(PIN)
        self.assertEqual(self.source.read_bytes(), RAW)

    def test_linked_or_public_input_rejected(self):
        self.source.chmod(0o644)
        with self.assertRaises(ValueError): T.enroll(self.source, PIN)
        self.source.chmod(0o600)
        link = self.root / 'link'; link.symlink_to(self.source)
        with self.assertRaises(OSError): T.enroll(link, PIN)
        link.unlink(); os.link(self.source, link)
        with self.assertRaises(ValueError): T.enroll(self.source, PIN)
        self.assertFalse(T.A.TRUST.exists())

    def test_wrong_current_key_or_changed_mode_refused(self):
        T.enroll(self.source, PIN)
        with self.assertRaises(ValueError): T.inspect('f' * 64)
        T.A.TRUST.chmod(0o644)
        with self.assertRaises(ValueError): T.inspect(PIN)


if __name__ == '__main__':
    unittest.main()
