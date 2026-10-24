# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)
sha = lambda data: hashlib.sha256(data).hexdigest()


class DependencyPolicyTests(unittest.TestCase):
    def test_cli_fixes_role_destination_and_pins(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier'), mock.patch.object(PROMOTE, '_install_dependencies') as install:
            for role, directory in (('gateway', 'preview-gateway'), ('display', 'waydroid-display')):
                PROMOTE.main(['--' + role + '-dependencies', '/source'])
                self.assertEqual(install.call_args.args[1:], ('/source', Path('/opt/droiddeck/runtime/' + directory), role))
            with self.assertRaises(ValueError):
                PROMOTE.main(['--gateway-dependencies', '/source', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class DependencyInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-dependencies-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.runtime, self.source = self.base / 'runtime', self.base / 'input'
        self.runtime.mkdir(mode=0o755)
        self.root = self.runtime / 'preview-gateway'
        self.root.mkdir(mode=0o755)
        self.source.mkdir(mode=0o700)
        baseline = []
        for name in sorted(('portable/worker-entry.js', 'preview-gateway/server.js', 'preview-gateway/package.json',
                            'preview-gateway/package-lock.json', 'waydroid-display/runtime-main.js', 'browser-connection/helper.js')):
            data = ('generated source ' + name).encode()
            if name.startswith('preview-gateway/'):
                p = self.root / name.split('/')[1]
                p.write_bytes(data)
                p.chmod(0o644)
            baseline.append({'path': name, 'size': len(data), 'sha256': sha(data)})
        m = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-source-component',
             'installable': False, 'sourceRevision': PROMOTE.RUNTIME_REVISION, 'files': baseline}
        raw = (json.dumps(m, indent=2) + '\n').encode()
        (self.runtime / 'bundle-manifest.json').write_bytes(raw)
        (self.runtime / 'bundle-manifest.json').chmod(0o644)
        self.base_pin = sha(raw)
        records = []
        for name, data in [('fixture/LICENSE', b'generated license'), ('fixture/empty', b''), ('fixture/index.js', b'generated non-executed script')]:
            p = self.source / name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(data)
            records.append({'path': name, 'size': len(data), 'sha256': sha(data)})
        self.policy = {'manifestSha256': sha((self.root / 'package.json').read_bytes()),
                       'lockSha256': sha((self.root / 'package-lock.json').read_bytes()),
                       'filesSha256': sha(json.dumps(records, separators=(',', ':')).encode())}
        m = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-dependency-component', 'installable': False,
             'role': 'gateway', **self.policy, 'files': records}
        raw = (json.dumps(m, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(raw)
        self.pin = sha(raw)

    def install(self):
        return PROMOTE._install_dependencies(self.copier, str(self.source), self.root, 'gateway',
                                             manifest_pin=self.pin, base_pin=self.base_pin, policy=self.policy)

    def test_private_copy_preserves_source_and_refuses_existing_target(self):
        old = os.umask(0o077)
        try:
            receipt = self.install()
        finally:
            os.umask(old)
        self.assertEqual(receipt['role'], 'gateway')
        self.assertEqual(receipt['files'], 3)
        self.assertFalse(receipt['readyForActivation'])
        self.assertFalse(receipt['pinnedBundleMatched'])
        for p in self.source.rglob('*'):
            q = self.root / 'node_modules' / p.relative_to(self.source)
            self.assertEqual(q.stat().st_mode & 0o777, 0o755 if p.is_dir() else 0o644)
            if p.is_file():
                self.assertEqual(p.read_bytes(), q.read_bytes())
                self.assertNotEqual(p.stat().st_ino, q.stat().st_ino)
        with self.assertRaises(ValueError):
            self.install()

    def test_changed_package_code_refuses_before_output(self):
        (self.root / 'server.js').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'node_modules').exists())

    def test_stage_and_git_source_metadata_keep_separate_exact_pins(self):
        self.policy['runtimeManifestSha256'] = self.policy['manifestSha256']
        self.policy['runtimeLockSha256'] = self.policy['lockSha256']
        self.policy['manifestSha256'] = sha(b'generated CRLF package metadata\r\n')
        self.policy['lockSha256'] = sha(b'generated CRLF lock metadata\r\n')
        m = json.loads((self.source / 'bundle-manifest.json').read_bytes())
        m['manifestSha256'], m['lockSha256'] = self.policy['manifestSha256'], self.policy['lockSha256']
        raw = (json.dumps(m, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(raw)
        self.pin = sha(raw)
        self.assertTrue(self.install()['promoted'])

    def test_midcopy_code_mutation_refuses_publication(self):
        copy = self.copier.copy_image
        def changed(*args):
            result = copy(*args)
            (self.root / 'server.js').write_bytes(b'changed')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=changed), self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'node_modules').exists())

    def test_raced_empty_target_is_not_replaced(self):
        rename = PROMOTE.rename_exclusive
        def raced(*args):
            (self.root / 'node_modules').mkdir()
            return rename(*args)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced), self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(list((self.root / 'node_modules').iterdir()), [])

    def test_dependency_bytes_and_extra_private_files_refuse(self):
        (self.source / 'key.pem').write_bytes(b'generated sentinel')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'node_modules').exists())


if __name__ == '__main__':
    unittest.main()
