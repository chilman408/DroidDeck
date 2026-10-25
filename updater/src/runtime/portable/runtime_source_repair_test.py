# SPDX-License-Identifier: GPL-3.0-only
"""Generated native fixtures only; no real runtime, Android, or service operations."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


R, P = module('runtime-source-repair'), module('promote-images')


@unittest.skipUnless(os.geteuid() == 0, 'root Linux private generated fixtures')
class Repair(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-source-repair-fixture-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.source, self.parent = (base / name for name in ('runtime', 'component', 'receipts'))
        for path, mode in ((self.root, 0o755), (self.source, 0o700), (self.parent, 0o700)):
            path.mkdir(mode=mode)
        self.c = P.load_copier()
        names = {'portable/worker-entry.js', 'preview-gateway/server.js',
                 'waydroid-display/runtime-main.js', 'browser-connection/helper.js'}
        names.update('portable/fixture-%02d.js' % i for i in range(71))
        self.files = {name: ('Generated harmless source ' + name + '\n').encode() for name in sorted(names)}
        for name, data in self.files.items():
            path = self.root / name
            path.parent.mkdir(mode=0o755, exist_ok=True)
            path.write_bytes(data); path.chmod(0o644)
        for name in ('waydroid-display', 'preview-gateway'):
            (self.root / name / 'node_modules').mkdir(mode=0o755)
        manifest = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-source-component',
            'installable': False, 'sourceRevision': P.RUNTIME_REVISION,
            'files': [{'path': name, 'size': len(data), 'sha256': R.sha(data)} for name, data in self.files.items()]}
        self.old = (json.dumps(manifest, indent=2) + '\n').encode()
        self.probe = b'Generated harmless missing import\n'
        manifest['files'].append({'path': R.LEAF, 'size': len(self.probe), 'sha256': R.sha(self.probe)})
        manifest['files'].sort(key=lambda value: value['path'])
        self.new = (json.dumps(manifest, indent=2) + '\n').encode()
        target = self.root / 'bundle-manifest.json'
        target.write_bytes(self.old); target.chmod(0o644)
        self.old_inode = target.stat().st_ino
        for name, data in (('bundle-manifest.json', self.new), ('probe.js', self.probe)):
            path = self.source / name
            path.write_bytes(data); path.chmod(0o600)
        self.pins = tuple(map(R.sha, (self.old, self.new, self.probe)))
        self.guard_calls = 0

    def guard(self):
        self.guard_calls += 1

    def run_repair(self, restoring=False, **kwargs):
        return R.repair(P, self.c, self.root, self.source, self.parent, restoring=restoring,
                        check=kwargs.get('check', self.guard), pins=self.pins)

    def assert_restored(self):
        self.assertEqual((self.root / 'bundle-manifest.json').read_bytes(), self.old)
        self.assertEqual((self.root / 'bundle-manifest.json').stat().st_ino, self.old_inode)
        self.assertFalse((self.root / R.LEAF).exists())
        for name, data in self.files.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_apply_and_owned_restore_preserve_both_generations(self):
        result = self.run_repair()
        self.assertEqual(result['sourceFiles'], 76)
        self.assertFalse(result['servicesActivated'])
        self.assertEqual((self.root / R.LEAF).read_bytes(), self.probe)
        receipt = self.parent / R.NAMESPACE
        self.assertEqual((receipt / 'manifest.exchange').stat().st_ino, self.old_inode)
        self.assertEqual((receipt / 'receipt.json').stat().st_mode & 0o777, 0o600)
        probe_inode = (self.root / R.LEAF).stat().st_ino
        self.assertTrue(self.run_repair(True)['restored'])
        self.assert_restored()
        self.assertEqual((receipt / 'retired-probe.js').stat().st_ino, probe_inode)
        self.assertEqual((receipt / 'manifest.exchange').read_bytes(), self.new)
        self.assertGreater(self.guard_calls, 10)

    def test_restore_idempotent_apply_not_replayable(self):
        self.run_repair()
        with self.assertRaises(ValueError):
            self.run_repair()
        self.run_repair(True)
        self.run_repair(True)
        self.assert_restored()
        with self.assertRaises(FileExistsError):
            self.run_repair()

    def test_busy_runtime_before_any_write(self):
        with self.assertRaises(RuntimeError):
            self.run_repair(check=lambda: (_ for _ in ()).throw(RuntimeError('generated busy')))
        self.assertFalse((self.parent / R.NAMESPACE).exists())
        self.assert_restored()

    def test_changed_existing_source_refused(self):
        (self.root / 'portable/worker-entry.js').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.run_repair()
        self.assertFalse((self.parent / R.NAMESPACE).exists())

    def test_extra_source_refused(self):
        (self.root / 'portable/foreign.js').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.run_repair()
        self.assertFalse((self.parent / R.NAMESPACE).exists())

    def test_wrong_new_pin_refused(self):
        (self.source / 'probe.js').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.run_repair()
        self.assertFalse((self.parent / R.NAMESPACE).exists())

    def test_hardlinked_source_refused(self):
        os.link(self.source / 'probe.js', self.source / 'linked')
        with self.assertRaises(ValueError):
            self.run_repair()
        self.assertFalse((self.parent / R.NAMESPACE).exists())

    def test_source_symlink_refused(self):
        path = self.source / 'probe.js'
        path.rename(self.source / 'original')
        path.symlink_to('original')
        with self.assertRaises(OSError):
            self.run_repair()

    def test_foreign_probe_inode_preserved_on_restore(self):
        self.run_repair()
        path = self.root / R.LEAF
        path.rename(self.root / 'waydroid-display/owned-preserved.js')
        path.write_bytes(self.probe); path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.run_repair(True)
        self.assertEqual(path.read_bytes(), self.probe)
        self.assertEqual((self.root / 'bundle-manifest.json').read_bytes(), self.new)

    def test_foreign_manifest_preserved(self):
        self.run_repair()
        path = self.root / 'bundle-manifest.json'
        path.rename(self.root / 'foreign-retained.json')
        path.write_bytes(self.new); path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.run_repair(True)
        self.assertEqual(path.read_bytes(), self.new)

    def test_foreign_receipt_refused(self):
        self.run_repair()
        path = self.parent / R.NAMESPACE / 'receipt.json'
        data = json.loads(path.read_bytes()); data['runtimeInode'] += 1
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.run_repair(True)
        self.assertEqual((self.root / R.LEAF).read_bytes(), self.probe)

    def test_interrupted_before_probe_publication_restores(self):
        with patch.object(R, 'rename', side_effect=OSError('generated interruption')):
            with self.assertRaises(OSError):
                self.run_repair()
        self.run_repair(True)
        self.assert_restored()

    def test_interrupted_before_manifest_exchange_restores(self):
        original = R.rename
        def interrupt(*args, **kwargs):
            if args[1] == 'manifest.exchange':
                raise OSError('generated interruption')
            return original(*args, **kwargs)
        with patch.object(R, 'rename', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.run_repair()
        self.assertTrue((self.root / R.LEAF).exists())
        self.run_repair(True)
        self.assert_restored()

    def test_interrupted_after_manifest_exchange_restores(self):
        original = R.rename
        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            if args[1] == 'manifest.exchange':
                raise OSError('generated post-exchange interruption')
        with patch.object(R, 'rename', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.run_repair()
        self.assertEqual((self.root / 'bundle-manifest.json').read_bytes(), self.new)
        self.run_repair(True)
        self.assert_restored()

    def test_guard_change_before_publication_retains_history(self):
        def changing():
            self.guard_calls += 1
            if (self.parent / R.NAMESPACE / 'receipt.json').exists():
                raise RuntimeError('generated concurrent runtime')
        with self.assertRaises(RuntimeError):
            self.run_repair(check=changing)
        self.assert_restored()
        self.assertTrue((self.parent / R.NAMESPACE / 'receipt.json').exists())
        self.run_repair(True)
        self.assert_restored()


if __name__ == '__main__':
    unittest.main(verbosity=2)
