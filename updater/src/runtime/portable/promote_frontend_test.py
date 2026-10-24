# SPDX-License-Identifier: GPL-3.0-only
# Root Linux fixtures use generated content, never Android data or certificates.
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


class FrontendPolicyTests(unittest.TestCase):
    def test_fixed_manifest_and_fresh_skeleton(self):
        self.assertTrue(hasattr(PROMOTE, 'FRONTEND_MANIFEST_SHA256'), 'Frontend installer missing')
        self.assertEqual(PROMOTE.FRONTEND_MANIFEST_SHA256, 'b7b943a36015737ceed2d03e084ebf3933784fe9ba75fd62c1877e942817a9ef')
        self.assertEqual(PROMOTE.ARTIFACT_RECEIPT_SHA256, '804d5fa13cb1e128f82a2cb056d5fa87f7a7f4c516b0c912af7c6b839b3360c9')

    def test_frontend_cli_has_no_target_or_pin_override(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier', return_value=mock.Mock()), mock.patch.object(PROMOTE, '_install_frontend', create=True) as install:
            PROMOTE.main(['--frontend', '/source'])
            self.assertEqual(install.call_args.args[1:], ('/source', Path('/opt/droiddeck')))
            for args in (['--frontend'], ['--frontend', '/source', '/override']):
                with self.assertRaises(ValueError):
                    PROMOTE.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux fixtures required')
class FrontendInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-frontend-install-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source, self.root = self.base / 'source', self.base / 'droiddeck'
        self.source.mkdir(mode=0o700)
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder'):
            (self.root / name).mkdir(mode=0o700)
        marker = b'generated skeleton marker'
        (self.root / 'artifact-layout.json').write_bytes(marker)
        (self.root / 'artifact-layout.json').chmod(0o600)
        self.base_pin = hashlib.sha256(marker).hexdigest()
        records = []
        for name, data in [('LICENSE', b'generated license'), ('index.html', b'generated HTML'), ('js/app.js', b'generated non-executed script')]:
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            records.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        value = {'version': 1, 'product': 'DroidDeck', 'productVersion': '0.1.0-preview',
                 'kind': 'frontend-component', 'installable': False,
                 'sourceRevision': '34763a62819a1dedad9d3eb62f1e175f2659091f',
                 'patchSha256': 'ca11610dbd330ef03e0111ea1e5379d17dd3b8a8757c6121fa2d61dd0a651373', 'files': records}
        encoded = (json.dumps(value, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(encoded)
        self.pin = hashlib.sha256(encoded).hexdigest()

    def install(self):
        return PROMOTE._install_frontend(self.copier, str(self.source), self.root,
                                        manifest_pin=self.pin, base_pin=self.base_pin)

    def test_private_copy_public_modes_and_honest_receipt(self):
        old = os.umask(0o077)
        try:
            receipt = self.install()
        finally:
            os.umask(old)
        self.assertEqual(receipt['files'], 3)
        self.assertFalse(receipt['pinnedBundleMatched'])
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['readyForActivation'])
        for p in (self.source).rglob('*'):
            target = self.root / 'frontend' / p.relative_to(self.source)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755 if p.is_dir() else 0o644)
            if p.is_file():
                self.assertEqual(target.read_bytes(), p.read_bytes())
                self.assertNotEqual(target.stat().st_ino, p.stat().st_ino)
                self.assertEqual((target.stat().st_uid, target.stat().st_gid, target.stat().st_nlink), (0, 0, 1))
        with self.assertRaises(ValueError):
            self.install()

    def test_wrong_manifest_or_bytes_and_private_extra_reject_before_output(self):
        (self.source / 'private-key.pem').write_bytes(b'generated sentinel, not a key')
        with self.assertRaises(ValueError):
            self.install()
        (self.source / 'private-key.pem').unlink()
        (self.source / 'js/app.js').write_bytes(b'wrong')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'frontend').exists())

    def test_linked_leaf_and_ancestor_reject(self):
        leaf = self.source / 'js/app.js'
        leaf.unlink()
        leaf.symlink_to(self.source / 'index.html')
        with self.assertRaises(ValueError):
            self.install()
        link = self.base / 'linked-source'
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            PROMOTE._install_frontend(self.copier, str(link), self.root, manifest_pin=self.pin, base_pin=self.base_pin)

    def test_source_replacement_during_copy_rejects(self):
        copy = self.copier.copy_image
        def changed(*args):
            result = copy(*args)
            leaf = self.source / 'index.html'
            data = leaf.read_bytes()
            leaf.unlink()
            leaf.write_bytes(data)
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse((self.root / 'frontend').exists())

    def test_corrupted_output_rejects(self):
        copy = self.copier.copy_image
        def corrupted(*args):
            fd, info = copy(*args)
            os.pwrite(fd, b'X', 0)
            return fd, info
        with mock.patch.object(self.copier, 'copy_image', side_effect=corrupted):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse((self.root / 'frontend').exists())

    def test_raced_empty_target_never_overwritten(self):
        rename = PROMOTE.rename_exclusive
        def raced(*args):
            (self.root / 'frontend').mkdir()
            return rename(*args)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced):
            with self.assertRaises(FileExistsError):
                self.install()
        self.assertEqual(list((self.root / 'frontend').iterdir()), [])

    def test_postpublish_uncertainty_retains_published_output(self):
        fsync = PROMOTE.os.fsync
        def failed(fd):
            if (self.root / 'frontend').exists():
                raise OSError('generated final sync failure')
            return fsync(fd)
        with mock.patch.object(PROMOTE.os, 'fsync', side_effect=failed):
            with self.assertRaises(PROMOTE.PromotionUncertain):
                self.install()
        self.assertTrue((self.root / 'frontend/index.html').exists())

    def test_wrong_manifest_rejects_before_output(self):
        with self.assertRaises(ValueError):
            PROMOTE._install_frontend(self.copier, str(self.source), self.root,
                                      manifest_pin='0' * 64, base_pin=self.base_pin)
        self.assertEqual(set(p.name for p in self.root.iterdir()),
                         {'guest', 'libgbinder', 'artifact-layout.json'})

    def test_changed_skeleton_during_copy_rejects(self):
        copy = self.copier.copy_image
        def changed(*args):
            result = copy(*args)
            (self.root / 'artifact-layout.json').write_bytes(b'changed generated marker')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse((self.root / 'frontend').exists())

    def test_prepublication_disk_failure_has_no_final_target(self):
        with mock.patch.object(self.copier, 'copy_image', side_effect=OSError('generated disk failure')):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse((self.root / 'frontend').exists())

    def test_hardlinked_leaf_rejected_before_output(self):
        os.link(self.source / 'index.html', self.base / 'extra-link')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'frontend').exists())


class FrontendManifestTests(unittest.TestCase):
    def test_unsafe_and_duplicate_paths_rejected(self):
        value = {'version': 1, 'product': 'DroidDeck', 'productVersion': '0.1.0-preview',
                 'kind': 'frontend-component', 'installable': False,
                 'sourceRevision': PROMOTE.FRONTEND_REVISION, 'patchSha256': PROMOTE.FRONTEND_PATCH}
        for path in ('../secret', '/root/file', 'js/../secret', 'js//file', 'bundle-manifest.json', 'LICENSE'):
            records = [{'path': name, 'size': 1, 'sha256': 'a' * 64}
                       for name in ('LICENSE', 'index.html', path)]
            with self.subTest(path=path), self.assertRaises(ValueError):
                PROMOTE.frontend_records(json.dumps({**value, 'files': records}).encode())

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):
            PROMOTE.frontend_records(b'{"version":1,"version":1}')


if __name__ == '__main__':
    unittest.main()
