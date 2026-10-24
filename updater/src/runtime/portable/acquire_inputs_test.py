# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request

spec = importlib.util.spec_from_file_location('publisher_inputs_tested', Path(__file__).with_name('acquire-inputs.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data) if length is None else length)}


class Policy(unittest.TestCase):
    def test_invalid_cli_does_not_allocate_or_fetch(self):
        with mock.patch.object(D, 'acquire') as acquire:
            for args in ([], ['--force', '/new'], ['--acquire-build-inputs', '/new', '--override']):
                with self.assertRaises(ValueError):
                    D.main(args)
        acquire.assert_not_called()

    def test_plaintext_and_credential_redirects_refuse(self):
        request = urllib.request.Request('https://publisher.invalid/artifact')
        for url in ('http://publisher.invalid/file', 'file:///private', 'https://user:pass@publisher.invalid/a',
                    'https://publisher.invalid:444/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                D.SecureRedirect().redirect_request(request, None, 302, '', {}, url)

    def test_sources_are_fixed_direct_publishers_and_known_pins(self):
        self.assertEqual(set(D.REPOSITORIES), {'libws', 'api', 'gbinder', 'glibutil', 'frontend'})
        self.assertEqual(set(D.DOWNLOADS), {'r8.jar', 'platform-36_r02.zip', 'scrcpy-server-v4.0', 'AuroraStore-4.8.4.apk'})
        for url, pin, maximum in D.DOWNLOADS.values():
            self.assertTrue(url.startswith('https://'))
            self.assertEqual(len(pin), 64)
            self.assertGreater(maximum, 0)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: 0)() >= 1000,
                     'Unprivileged Linux download fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home(), prefix='droiddeck-acquisition-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)

    def download(self, raw=b'publisher fixture', expected=None, length=None, maximum=4096):
        return D.download(self.fd, 'input.jar', 'https://publisher.invalid/input.jar',
                          expected or hashlib.sha256(raw).hexdigest(), maximum,
                          opener=lambda *a, **k: Response(raw, length))

    def test_download_exact_digest_and_no_overwrite(self):
        result = self.download()
        self.assertEqual((self.root / 'input.jar').read_bytes(), b'publisher fixture')
        self.assertEqual(result['sha256'], hashlib.sha256(b'publisher fixture').hexdigest())
        original = (self.root / 'input.jar').stat().st_ino
        with self.assertRaises(FileExistsError):
            self.download()
        self.assertEqual((self.root / 'input.jar').stat().st_ino, original)

    def test_digest_failure_retains_partial_without_ready_payload(self):
        with self.assertRaises(ValueError):
            self.download(expected='0' * 64)
        self.assertFalse((self.root / 'input.jar').exists())
        self.assertEqual((self.root / 'input.jar.partial').read_bytes(), b'publisher fixture')
        with self.assertRaises(FileExistsError):
            self.download()

    def test_wrong_length_and_oversize_refuse(self):
        with self.assertRaises(ValueError):
            self.download(length=1)
        self.assertFalse((self.root / 'input.jar').exists())
        with self.assertRaises(ValueError):
            D.download(self.fd, 'other.jar', 'https://publisher.invalid/a', '0' * 64, 1,
                       opener=lambda *a, **k: Response(b'oversized'))
        self.assertFalse((self.root / 'other.jar').exists())

    def test_existing_symlink_never_followed(self):
        sentinel = self.root / 'keep'
        sentinel.write_bytes(b'keep')
        (self.root / 'input.jar.partial').symlink_to(sentinel)
        with self.assertRaises(FileExistsError):
            self.download()
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_failed_acquisition_preserves_intent_and_refuses_existing_workspace(self):
        target = self.root / 'new'
        def fail(*args):
            raise OSError('network unavailable fixture')
        with self.assertRaises(OSError):
            D.acquire(D.MODES[1], str(target), fetch=fail)
        self.assertTrue((target / 'intent.json').is_file())
        self.assertFalse((target / 'ready.json').exists())
        with self.assertRaises(FileExistsError):
            D.acquire(D.MODES[1], str(target), fetch=fail)

    def test_complete_image_acquisition_is_closed_and_never_redistributable(self):
        target = self.root / 'new'
        def fetch(directory, name, url, expected, bound):
            D.write_new(directory, name, b'fixture image archive')
            return {'name': name, 'sha256': expected, 'size': 21, 'publisher': url}
        result = D.acquire(D.MODES[1], str(target), fetch=fetch)
        self.assertFalse(result['installable'])
        self.assertFalse(result['redistribute'])
        self.assertFalse(result['servicesActivated'])
        self.assertEqual(json.loads((target / 'ready.json').read_bytes()), result)
        self.assertEqual(set(p.name for p in target.iterdir()), {'intent.json', 'ready.json',
            *(row['filename'] for row in D.IMAGES['inputs'].values())})

    def test_git_subprocess_has_no_shell_or_user_config(self):
        response = subprocess.CompletedProcess([], 0, stdout=b'fixed commit\n', stderr=b'')
        with mock.patch.object(D.subprocess, 'run', return_value=response) as run:
            self.assertEqual(D.git(['rev-parse', 'HEAD']), b'fixed commit\n')
        args, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(args[0], '/usr/bin/git')
        self.assertIn('core.hooksPath=/dev/null', args)
        self.assertEqual(options['env']['GIT_CONFIG_GLOBAL'], '/dev/null')
        self.assertNotIn('shell', options)


if __name__ == '__main__':
    unittest.main()
