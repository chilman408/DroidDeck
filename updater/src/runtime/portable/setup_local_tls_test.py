# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('local_tls_tested', Path(__file__).with_name('setup-local-tls.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_invalid_cli_redacts_and_does_not_read_configuration(self):
        for args in ([], ['--force'], ['--initialize-local-tls', '/private/key']):
            with mock.patch.object(D, 'policy') as policy, self.assertRaises(ValueError):
                D.main(args)
            policy.assert_not_called()
        result = subprocess.run([sys.executable, '-I', '-B', D.__file__, '--force', 'private-secret'],
                                capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertNotIn(b'private-secret', result.stderr)


@unittest.skipUnless(sys.platform == 'linux', 'Linux local openssl/node fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        # Fixtures contain newly generated disposable keys only, never live keys.
        temporary = tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='droiddeck-local-tls-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.preferences = self.root / 'droiddeck'
        self.config = {'version': 1, 'user': 'fixture', 'uid': 1000, 'gid': 1000,
                       'home': '/home/fixture', 'hostname': 'droiddeck-test.invalid',
                       'lanAddress': '192.168.2.215', 'port': 8443}
        self.uid, self.gid = os.getuid(), os.getgid()
        self.config['uid'], self.config['gid'] = self.uid, self.gid
        # Config normalization requires a normal non-root account. This test uses
        # unprivileged Linux; root tests skip rather than weaken production policy.
        if self.uid < 1000 or self.gid < 1000:
            self.skipTest('Unprivileged Linux fixture required')

    def run_phase(self, mode=D.MODES[0], **kwargs):
        return D.initialize(self.config, str(self.preferences), mode, **kwargs)

    def assert_pair(self, result):
        self.assertTrue(result['pairPublished'])
        self.assertTrue(result['localTransportReady'])
        self.assertFalse(result['browserTrustVerified'])
        self.assertFalse(result['certificateTrustVerified'])
        self.assertFalse(result['privateKeyExported'])
        self.assertFalse(result['servicesActivated'])
        self.assertNotIn('BEGIN PRIVATE', str(result))

    def identities(self):
        return {name: (self.preferences / name).stat().st_ino for name in ('key.pem', 'cert.pem')}

    def test_initialize_verify_and_explicit_resume_preserve_pair(self):
        result = self.run_phase()
        self.assert_pair(result)
        original = self.identities()
        with mock.patch.object(D, 'rename_new', side_effect=AssertionError('inspect mutated')):
            self.assertEqual(self.run_phase(D.MODES[2]), result)
        self.assertEqual(self.run_phase(D.MODES[1]), result)
        self.assertEqual(self.identities(), original)
        with self.assertRaises(ValueError):
            self.run_phase()
        intent = (self.preferences / D.STAGE / 'intent.json').read_text()
        self.assertNotIn('PRIVATE KEY', intent)
        self.assertNotIn('sha256', D.json.loads(intent)['files']['key.pem'])

    def test_real_https_with_explicit_public_certificate_trust(self):
        import socket
        import ssl
        import threading
        self.run_phase()
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.minimum_version = ssl.TLSVersion.TLSv1_2
        server.load_cert_chain(self.preferences / 'cert.pem', self.preferences / 'key.pem')
        client = ssl.create_default_context(cafile=str(self.preferences / 'cert.pem'))
        failures = []
        with socket.socket() as listener:
            listener.settimeout(5)
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)

            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection, server.wrap_socket(connection, server_side=True) as transport:
                        self.assertTrue(transport.recv(4096).startswith(b'GET / HTTP/1.1\r\n'))
                        transport.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                except BaseException as error:
                    failures.append(error)
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                with socket.create_connection(listener.getsockname(), timeout=5) as connection, \
                        client.wrap_socket(connection, server_hostname=self.config['hostname']) as transport:
                    self.assertIn(('DNS', self.config['hostname']), transport.getpeercert()['subjectAltName'])
                    transport.sendall(b'GET / HTTP/1.1\r\nHost: droiddeck-test.invalid\r\nConnection: close\r\n\r\n')
                    response = b''
                    while chunk := transport.recv(4096):
                        response += chunk
                    self.assertEqual(response, b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            finally:
                worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])

    def die_and_resume(self, boundary):
        child = os.fork()
        if child == 0:
            try:
                options = {'after_intent': lambda: os._exit(73)} if boundary == 'intent' else {
                    'after_publish': lambda name: os._exit(73) if name == boundary else None}
                self.run_phase(**options)
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        before = {}
        for name in ('key.pem', 'cert.pem'):
            target = self.preferences / name
            staged = self.preferences / D.STAGE / name
            before[name] = (target if target.exists() else staged).stat().st_ino
        if boundary != 'cert.pem':
            with self.assertRaises(ValueError):
                self.run_phase(D.MODES[2])
        with mock.patch.object(D.subprocess, 'run', wraps=D.subprocess.run) as run:
            self.assert_pair(self.run_phase(D.MODES[1]))
        self.assertFalse(any('/usr/bin/openssl' in call.args[0] for call in run.call_args_list))
        self.assertEqual(self.identities(), before)

    def test_actual_death_after_intent_resumes_without_key_regeneration(self):
        self.die_and_resume('intent')

    def test_actual_death_between_pair_publications_resumes(self):
        self.die_and_resume('key.pem')

    def test_actual_death_after_both_publications_resumes(self):
        self.die_and_resume('cert.pem')

    def test_existing_or_partial_pair_is_never_replaced(self):
        self.preferences.mkdir(mode=0o700)
        target = self.preferences / 'key.pem'
        target.write_bytes(b'foreign key fixture')
        target.chmod(0o600)
        with self.assertRaises(ValueError):
            self.run_phase()
        self.assertEqual(target.read_bytes(), b'foreign key fixture')
        self.assertFalse((self.preferences / D.STAGE).exists())

    def test_empty_foreign_or_corrupt_journal_is_not_adopted(self):
        self.preferences.mkdir(mode=0o700)
        staged = self.preferences / D.STAGE
        staged.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.run_phase()
        with self.assertRaises(FileNotFoundError):
            self.run_phase(D.MODES[1])
        (staged / 'intent.json').write_bytes(b'{')
        (staged / 'intent.json').chmod(0o600)
        with self.assertRaises(ValueError):
            self.run_phase(D.MODES[1])

    def test_foreign_same_byte_inode_replacement_refuses(self):
        self.run_phase()
        target = self.preferences / 'cert.pem'
        replacement = self.root / 'foreign'
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(target)
        with self.assertRaises(ValueError):
            self.run_phase(D.MODES[1])

    def test_wrong_san_key_mismatch_or_unsafe_paths_refuse(self):
        self.run_phase()
        key = (self.preferences / 'key.pem').read_text()
        cert = (self.preferences / 'cert.pem').read_text()
        for config in ({**self.config, 'hostname': 'other.invalid'},
                       {**self.config, 'lanAddress': '192.168.2.99'}):
            with self.assertRaises(subprocess.CalledProcessError):
                D.policy(['--validate'], D.encode({'configuration': config, 'key': key, 'certificate': cert}))
        with self.assertRaises(subprocess.CalledProcessError):
            D.policy(['--validate'], D.encode({'configuration': self.config, 'key': 'invalid', 'certificate': cert}))
        self.preferences.chmod(0o755)
        with self.assertRaises(ValueError):
            self.run_phase(D.MODES[2])


if __name__ == '__main__':
    unittest.main()
