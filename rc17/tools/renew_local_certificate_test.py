# SPDX-License-Identifier: GPL-3.0-only
"""Native OpenSSL regression tests; generated keys stay in temporary fixtures."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('renew_local_certificate', Path(__file__).with_name('renew-local-certificate.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droidmate-tls-renew-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.root.chmod(0o700)
        self.key = self.root/'key.pem'; self.cert = self.root/'cert.pem'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc',
            '-keyout', str(self.key), '-out', str(self.cert), '-days', '1', '-subj', '/CN=Generated Fixture',
            '-addext', 'subjectAltName=IP:192.0.2.1,DNS:fixture.test',
            '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'extendedKeyUsage=serverAuth'],
            check=True, capture_output=True)
        self.key.chmod(0o600); self.cert.chmod(0o600)

    def test_renew_then_noop_preserves_key_and_san(self):
        key = self.key.read_bytes(); identity = self.key.stat().st_ino
        old = self.cert.read_bytes()
        self.assertTrue(m.renew(self.root, '192.0.2.1')['renewed'])
        self.assertEqual(key, self.key.read_bytes()); self.assertEqual(identity, self.key.stat().st_ino)
        extensions = m.run(['x509', '-in', str(self.cert), '-noout', '-ext', 'subjectAltName']).stdout
        self.assertIn(b'DNS:fixture.test', extensions); self.assertIn(b'192.0.2.1', extensions)
        m.run(['verify', '-CAfile', str(self.cert), '-purpose', 'sslserver', '-verify_ip', '192.0.2.1', str(self.cert)])
        self.assertEqual((self.root/('cert-before-renewal-'+hashlib.sha256(old).hexdigest()+'.pem')).read_bytes(),old)
        new = self.cert.read_bytes(); cert_identity = self.cert.stat().st_ino
        self.assertFalse(m.renew(self.root, '192.0.2.1')['renewed'])
        self.assertEqual(new,self.cert.read_bytes()); self.assertEqual(cert_identity,self.cert.stat().st_ino)

    def test_wrong_address_retains_pair(self):
        before = self.cert.read_bytes()
        with self.assertRaises(ValueError):m.renew(self.root,'192.0.2.2')
        self.assertEqual(before,self.cert.read_bytes())
        self.assertEqual(sorted(x.name for x in self.root.iterdir()),['cert.pem','key.pem'])

    def test_key_symlink_refused(self):
        self.key.rename(self.root/'original-key');self.key.symlink_to('original-key')
        before=self.cert.read_bytes()
        with self.assertRaises(OSError):m.renew(self.root,'192.0.2.1')
        self.assertEqual(before,self.cert.read_bytes())

    def test_key_mismatch_refused(self):
        other=self.root/'other-key'
        subprocess.run(['/usr/bin/openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(other)],check=True,capture_output=True)
        self.key.write_bytes(other.read_bytes());before=self.cert.read_bytes()
        with self.assertRaises(ValueError):m.renew(self.root,'192.0.2.1')
        self.assertEqual(before,self.cert.read_bytes())


if __name__=='__main__': unittest.main()
