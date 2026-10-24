# SPDX-License-Identifier: GPL-3.0-only
import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pilot', Path(__file__).with_name('prepare-github-pilot.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Privacy(unittest.TestCase):
    def setUp(self):
        self.generated = b'GeneratedTestCredential14'
        self.pins = P.fingerprints([str(len(self.generated)) + ':' + P.sha(self.generated),
                                    '14:' + P.sha(b'OtherFakeValue!')])

    def test_plain_embedded_base64_url_and_unicode_credentials_are_blocked_without_values(self):
        values = [self.generated, b'prefix' + self.generated + b'suffix',
                  base64.b64encode(self.generated),
                  ''.join('%%%02X' % byte for byte in self.generated).encode(),
                  self.generated.decode().encode('utf-16')]
        for raw in values:
            self.assertEqual(P.scan(raw, self.pins), ['supplied-administration-credential'])

    def test_private_key_and_provider_tokens_are_blocked(self):
        for kind in (b'OPENSSH ', b'RSA ', b'EC ', b''):
            self.assertIn('private-key-block', P.scan(b'-----BEGIN ' + kind + b'PRIVATE KEY-----\n' + b'A' * 48, self.pins))
        self.assertEqual(P.scan(rb'/^-----BEGIN PRIVATE KEY-----[A-Za-z0-9+/=\r\n]+/', self.pins), [])
        self.assertIn('provider-access-token', P.scan(b'ghp_' + b'A' * 36, self.pins))
        self.assertEqual(P.scan(b'password input is requested interactively; no credential stored', self.pins), [])

    def test_fingerprints_require_two_distinct_bounded_hashes_not_plaintext(self):
        for values in ([], ['password'], ['14:' + 'a' * 64], ['0:' + 'a' * 64, '999:' + 'b' * 64]):
            with self.assertRaises(ValueError): P.fingerprints(values)

    def test_findings_never_create_or_overwrite_publication_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('README.md', 'SECURITY.md', 'docs/PILOT.md', 'docs/KNOWN_LIMITATIONS.md', '.gitignore'):
                target = root / 'templates' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'public fixture')
            release = {'COPYING': b'license', 'INSTALL.md': b'install'}
            with patch.object(P, 'inspect_release', return_value=(release, {'source.txt': self.generated})):
                with self.assertRaises(ValueError):
                    P.prepare(root, root / 'output', self.pins, root / 'templates')
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
