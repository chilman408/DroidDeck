# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
FILE = HERE / 'runtime-candidate-admission.py'
P = None
if FILE.exists():
    spec = importlib.util.spec_from_file_location('candidate_admission', FILE)
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)


def manifest_bytes():
    source = Path(os.environ['DROIDDECK_CANDIDATE_TEST_INPUTS'])
    raw = (source / 'base/bundle-manifest.json').read_bytes()
    base = json.loads(raw)
    overlays = {
        'portable/gpu-access.js': (11805, '4cbc6ef0c155d56e26bcf91f68db896e073f035471b6b5bcec8e0dfb029557e0'),
        'portable/desktop-preflight.js': (1699, 'f562f47c7a1ccce89df8660b72a404c121a0b94691c7ab0b1fc4e53cd299654a'),
        'waydroid-display/headless-session-preflight.js': (5540, '83e3f626aa17e0541c85a7db0a2d53b95176647d944f0324e22e2ad1a5c1c0a9'),
    }
    records = [dict(row) for row in base['files']]
    for row in records:
        if row['path'] in overlays:
            row['size'], row['sha256'] = overlays[row['path']]
    candidate = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-source-candidate',
                 'baseSourceRevision': '76c89fbb8de9a672a9ec843808f9eca6fd8c9528',
                 'baseManifestSha256': '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71',
                 'guardSourceRevision': '5301fea5f07cf34e4bdd46ffc477693f0778b39d',
                 'installable': False, 'readyForActivation': False, 'servicesActivated': False,
                 'androidOperationsExecuted': False, 'files': records}
    return (json.dumps(candidate, indent=2) + '\n').encode('ascii')


class Boundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Independent held runtime candidate admission required')

    def test_independent_manifest_pin_and_closed_seventy_six_records(self):
        raw = manifest_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), P.MANIFEST_SHA256)
        records = P.candidate_records(raw)
        self.assertEqual(len(records), 76)
        self.assertEqual(records['portable/gpu-access.js'][0], 11805)
        self.assertNotIn('bundle-manifest.json', records)
        for flag in P.FALSE_FLAGS:
            self.assertFalse(P.policy((0, 0))[flag])

    def test_manifest_is_not_its_own_authority(self):
        raw = manifest_bytes()
        for changed in (b'', b'{}', raw + b' ', raw.replace(b'11805', b'11806'), raw[:-1], b'x' * 65537):
            with self.assertRaises(ValueError):
                P.candidate_records(changed)

    def test_privilege_canonical_owner_and_dispatch_before_filesystem_reads(self):
        with ExitStack() as stack, mock.patch.object(P.os, 'open', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_candidate(stack, '/root/generated')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for root in ('/', 'relative', '//root/source', '/root//source', '/root/source/', '/root/../source', '/root/\x00source'):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_candidate(stack, root)
                for owner in ([1000, 1000], (True, 1000), (0, 1000), (1000, 0), (-1, 2), (2**31, 2)):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_candidate(stack, '/root/generated', owner=owner)
        with mock.patch.object(P, 'hold_runtime_candidate', side_effect=AssertionError('no reads')):
            for args in ([], ['--install'], ['--stage'], ['--verify-package'], ['--verify-package', '/root/x', '--force']):
                with self.assertRaises(ValueError):
                    P.main(args)

    def test_user_policy_rejects_root_aliases_and_outside_home_before_reads(self):
        with ExitStack() as stack, mock.patch.object(P, '_root_linux'), \
                mock.patch.object(P, '_account', return_value=('hilman', 1000, 1000, '/home/hilman')), \
                mock.patch.object(P.os, 'open', side_effect=AssertionError('no reads')):
            for user, root in (('root', '/home/hilman/x'), ('../x', '/home/hilman/x'), ('hilman', '/home/other/x'),
                               ('hilman', '/home/hilman'), ('hilman', '/home/hilman-suffix/x')):
                with self.assertRaises(ValueError):
                    P.hold_user_candidate(stack, user, root)

    def test_cli_failure_is_fixed_and_secret_free(self):
        result = subprocess.run([sys.executable, '-B', str(FILE), '--activate', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime candidate admission refused; no changes performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only file-descriptor fixtures required')
class HeldCandidate(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Independent held runtime candidate admission required')
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-candidate-admission-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'candidate'
        self.source.mkdir(mode=0o700)
        source = Path(os.environ['DROIDDECK_CANDIDATE_TEST_INPUTS'])
        raw = manifest_bytes()
        for row in json.loads(raw)['files']:
            relative = row['path']
            overlay = source / 'guard' / relative
            target = self.source / relative
            target.parent.mkdir(mode=0o700, exist_ok=True)
            target.write_bytes((overlay if overlay.exists() else source / 'base' / relative).read_bytes())
            target.chmod(0o600)
        (self.source / P.MANIFEST).write_bytes(raw)
        (self.source / P.MANIFEST).chmod(0o600)

    def hold(self, stack):
        return P.hold_runtime_candidate(stack, self.source)

    def test_all_bytes_held_read_only_and_fds_close(self):
        before = set(os.listdir('/proc/self/fd'))
        with mock.patch.object(P.os, 'open', wraps=os.open) as opened, ExitStack() as stack:
            held = self.hold(stack)
            self.assertEqual(len(held['files']), 77)
            for name in held['files']:
                self.assertEqual(held['read'](name), (self.source / name).read_bytes())
            held['check']()
            self.assertTrue(held['policy']['rootPrivateSourceCandidate'])
            with self.assertRaises(ValueError):
                held['read']('../private')
            for call in opened.call_args_list:
                self.assertEqual(call.args[1] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), 0)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_corrupt_extra_hardlink_symlink_and_bad_modes_refuse(self):
        file = self.source / 'portable/gpu-access.js'
        original = file.read_bytes()
        file.write_bytes(original + b'changed')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        file.write_bytes(original)
        extra = self.source / 'extra'
        extra.touch(mode=0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        extra.unlink()
        os.link(file, self.root / 'linked')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        (self.root / 'linked').unlink()
        file.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        file.chmod(0o600)
        file.rename(self.root / 'retained')
        file.symlink_to(self.root / 'retained')
        with ExitStack() as stack, self.assertRaises(OSError):
            self.hold(stack)

    def test_held_file_replacement_and_metadata_drift_refuse(self):
        file = self.source / 'portable/gpu-access.js'
        with ExitStack() as stack:
            held = self.hold(stack)
            file.rename(self.root / 'retained')
            shutil.copyfile(self.root / 'retained', file)
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                held['read']('portable/gpu-access.js')
        with ExitStack() as stack:
            held = self.hold(stack)
            original = file.stat()
            file.write_bytes(file.read_bytes())
            os.utime(file, ns=(original.st_atime_ns, original.st_mtime_ns))
            with self.assertRaises(ValueError):
                held['check']()

    def test_original_directory_and_ancestor_generation_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            directory = self.source / 'portable'
            directory.rename(self.source / 'old-portable')
            shutil.copytree(self.source / 'old-portable', directory)
            with self.assertRaises(ValueError):
                held['check']()
        shutil.rmtree(self.source / 'old-portable')
        with ExitStack() as stack:
            held = self.hold(stack)
            self.source.rename(self.root / 'retained')
            shutil.copytree(self.root / 'retained', self.source)
            with self.assertRaises(ValueError):
                held['check']()

    def test_held_inventory_and_in_place_edit_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            extra = self.source / 'portable/extra'
            extra.touch(mode=0o600)
            with self.assertRaises(ValueError):
                held['check']()
            extra.unlink()
            file = self.source / 'portable/gpu-access.js'
            with file.open('r+b') as stream:
                stream.write(b'x')
            with self.assertRaises(ValueError):
                held['check']()

    def test_directory_mode_drift_and_symlinked_ancestor_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            (self.source / 'portable').chmod(0o755)
            with self.assertRaises(ValueError):
                held['check']()
        (self.source / 'portable').chmod(0o700)
        (self.root / 'alias').symlink_to(self.source, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises(OSError):
            P.hold_runtime_candidate(stack, self.root / 'alias')

    def test_nonregular_manifest_is_nonblocking_and_rejected(self):
        manifest = self.source / P.MANIFEST
        manifest.unlink()
        os.mkfifo(manifest, 0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_private_owner_binding_and_account_drift(self):
        for item in [self.source, *self.source.rglob('*')]:
            os.chown(item, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        with ExitStack() as stack:
            held = P.hold_runtime_candidate(stack, self.source, owner=(1000, 1000))
            self.assertFalse(held['policy']['rootPrivateSourceCandidate'])
            held['check']()
        with ExitStack() as stack, mock.patch.object(P, '_account', return_value=('hilman', 1000, 1000, str(self.root))) as account:
            held = P.hold_user_candidate(stack, 'hilman', self.source)
            account.return_value = ('hilman', 1001, 1000, str(self.root))
            with self.assertRaises(ValueError):
                held['check']()

    def test_partial_admission_failure_closes_every_descriptor(self):
        (self.source / 'waydroid-display/touch-adapter.js').write_bytes(b'changed')
        before = set(os.listdir('/proc/self/fd'))
        with self.assertRaises(ValueError), ExitStack() as stack:
            self.hold(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
