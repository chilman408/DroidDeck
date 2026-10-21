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


class RuntimePolicyTests(unittest.TestCase):
    def test_fixed_source_and_cli_boundary(self):
        self.assertEqual(PROMOTE.RUNTIME_MANIFEST_SHA256, 'c9cdeb4d5b2d74216253404336ff6671d3ae390e524e0a173714e5d7b0c8bd4c')
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier'), mock.patch.object(PROMOTE, '_install_runtime') as install:
            PROMOTE.main(['--runtime', '/source'])
            self.assertEqual(install.call_args.args[1:], ('/source', Path('/opt/droiddeck')))
            with self.assertRaises(ValueError):
                PROMOTE.main(['--runtime', '/source', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class RuntimeInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-runtime-install-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source, self.root = self.base / 'source', self.base / 'droiddeck'
        self.source.mkdir(mode=0o700)
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder', 'frontend'):
            (self.root / name).mkdir(mode=0o755)
        marker = b'generated artifact marker'
        (self.root / 'artifact-layout.json').write_bytes(marker)
        (self.root / 'artifact-layout.json').chmod(0o600)
        self.base_pin = hashlib.sha256(marker).hexdigest()
        frontend = b'generated frontend marker'
        (self.root / 'frontend/bundle-manifest.json').write_bytes(frontend)
        (self.root / 'frontend/bundle-manifest.json').chmod(0o644)
        self.frontend_pin = hashlib.sha256(frontend).hexdigest()
        records = []
        for name in sorted(('portable/worker-entry.js', 'preview-gateway/server.js', 'waydroid-display/runtime-main.js', 'browser-connection/helper.js')):
            data = ('generated non-executed ' + name).encode()
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            records.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        m = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-source-component',
             'installable': False, 'sourceRevision': PROMOTE.RUNTIME_REVISION, 'files': records}
        encoded = (json.dumps(m, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(encoded)
        self.pin = hashlib.sha256(encoded).hexdigest()

    def install(self):
        return PROMOTE._install_runtime(self.copier, str(self.source), self.root,
                                        manifest_pin=self.pin, base_pin=self.base_pin, frontend_pin=self.frontend_pin)

    def test_source_install_preserves_existing_frontend_and_refuses_repeat(self):
        receipt = self.install()
        self.assertEqual(receipt['kind'], 'protected-runtime-install')
        self.assertEqual(receipt['files'], 4)
        self.assertFalse(receipt['pinnedBundleMatched'])
        self.assertFalse(receipt['readyForActivation'])
        self.assertEqual((self.root / 'frontend/bundle-manifest.json').read_bytes(), b'generated frontend marker')
        for p in self.source.rglob('*'):
            q = self.root / 'runtime' / p.relative_to(self.source)
            self.assertEqual(q.stat().st_mode & 0o777, 0o755 if p.is_dir() else 0o644)
            if p.is_file():
                self.assertEqual(p.read_bytes(), q.read_bytes())
        with self.assertRaises(ValueError):
            self.install()

    def test_wrong_or_linked_frontend_rejects_before_output(self):
        p = self.root / 'frontend/bundle-manifest.json'
        p.write_bytes(b'wrong')
        with self.assertRaises(ValueError):
            self.install()
        p.unlink()
        p.symlink_to(self.root / 'artifact-layout.json')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'runtime').exists())

    def test_frontend_mutation_during_copy_rejects(self):
        copy = self.copier.copy_image
        def changed(*args):
            result = copy(*args)
            (self.root / 'frontend/bundle-manifest.json').write_bytes(b'changed')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=changed), self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'runtime').exists())


if __name__ == '__main__':
    unittest.main()
