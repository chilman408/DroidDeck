# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

s = importlib.util.spec_from_file_location('update_installer_tested', Path(__file__).with_name('update-installer-entry.py'))
D = importlib.util.module_from_spec(s); s.loader.exec_module(D)

def tar(rows):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as t:
        for name, raw, mode, kind in rows:
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = mode; info.type = kind
            t.addfile(info, io.BytesIO(raw))
    return out.getvalue()

class Sources(unittest.TestCase):
    def fixture(self):
        name = 'runtime/portable/gateway-update-release.py'; raw = b'# generated fixture only\n'
        manifest = {'schema': 1, 'kind': 'droiddeck-runtime-update-source', 'sourceRevision': 'c' * 40,
            'version': '0.1.1-rc.5', 'installerIncluded': False,
            'files': [{'path': name, 'size': len(raw), 'sha256': D.sha(raw)}]}
        rows = [(D.PREFIX + 'source-manifest.json', D.encode(manifest), 0o644, tarfile.REGTYPE),
                (D.PREFIX + 'src/' + name, raw, 0o644, tarfile.REGTYPE)]
        return manifest, rows

    def test_closed_regular_committed_source_archive(self):
        manifest, rows = self.fixture()
        observed, files = D.sources(tar(rows), manifest['sourceRevision'], manifest['version'])
        self.assertEqual(observed, manifest)
        self.assertEqual(files['runtime/portable/gateway-update-release.py'], b'# generated fixture only\n')

    def test_traversal_link_duplicate_and_private_source_members_refuse(self):
        manifest, rows = self.fixture()
        for bad in ((D.PREFIX + 'src/../../outside.py', b'bad', 0o644, tarfile.REGTYPE),
                    (D.PREFIX + 'src/runtime/private.py', b'', 0o644, tarfile.SYMTYPE), rows[-1],
                    (D.PREFIX + 'src/runtime/key.pem', b'bad', 0o644, tarfile.REGTYPE)):
            with self.subTest(name=bad[0]):
                with self.assertRaises(ValueError): D.sources(tar([*rows, bad]), manifest['sourceRevision'], manifest['version'])

    def test_changed_source_digest_and_wrong_release_refuse(self):
        manifest, rows = self.fixture()
        changed = rows[:-1] + [(rows[-1][0], b'foreign fixture', 0o644, tarfile.REGTYPE)]
        with self.assertRaises(ValueError): D.sources(tar(changed), manifest['sourceRevision'], manifest['version'])
        with self.assertRaises(ValueError): D.sources(tar(rows), 'd' * 40, manifest['version'])

    def test_signature_failure_precedes_any_source_parsing_or_privileged_staging(self):
        entries = {'source.tar': b'foreign', 'update.tar': b'foreign', 'statement.json': b'{}\n', 'signature.bin': b'x' * 64}
        with patch.object(D, 'verify_signature', side_effect=ValueError('generated bad signature')) as verify, \
                patch.object(D, 'sources') as source:
            with self.assertRaises(ValueError): D.authenticate(entries)
            verify.assert_called_once(); source.assert_not_called()

@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Root Linux original extraction fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='droiddeck-updater-package-fixture-', dir='/root')
        self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.destination = self.root / 'new-package'
        self.files = {'src/runtime/portable/generated.py': b'# generated fixture\n', 'source.tar': b'public fixture archive\n',
                      'update.tar': b'public fixture update\n', 'statement.json': b'{}\n', 'signature.bin': b'x' * 64}

    def test_exclusive_staging_readonly_reuse_and_equal_byte_replacement_refusal(self):
        D.materialize(self.destination, self.files)
        before = (self.destination / 'src/runtime/portable/generated.py').stat()
        D.materialize(self.destination, self.files)
        self.assertEqual((self.destination / 'src/runtime/portable/generated.py').stat(), before)
        path = self.destination / 'src/runtime/portable/generated.py'; raw = path.read_bytes()
        path.rename(path.with_name('retained-original.py')); path.write_bytes(raw); path.chmod(0o600)
        with self.assertRaises(ValueError): D.materialize(self.destination, self.files)

    def test_partial_publication_is_retained_and_refuses_replay(self):
        self.destination.mkdir(mode=0o700)
        (self.destination / 'intent.json').write_bytes(b'generated partial fixture\n')
        (self.destination / 'intent.json').chmod(0o600)
        with self.assertRaises((ValueError, FileNotFoundError)): D.materialize(self.destination, self.files)
        self.assertFalse((self.destination / 'complete.json').exists())

    def test_linked_destination_cannot_redirect_root_writes(self):
        outside = self.root / 'outside'; outside.mkdir(mode=0o700)
        self.destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)): D.materialize(self.destination, self.files)
        self.assertEqual(list(outside.iterdir()), [])

    def test_foreign_empty_directory_cannot_extend_original_namespace(self):
        D.materialize(self.destination,self.files)
        (self.destination/'foreign-empty').mkdir(mode=0o700)
        with self.assertRaises(ValueError):D.materialize(self.destination,self.files)

    def test_same_files_in_replacement_directory_do_not_transfer_directory_ownership(self):
        D.materialize(self.destination,self.files)
        original=self.destination/'src/runtime/portable'
        retained=self.root/'retained-directory';original.rename(retained)
        original.mkdir(mode=0o700)
        (retained/'generated.py').rename(original/'generated.py')
        with self.assertRaises(ValueError):D.materialize(self.destination,self.files)

if __name__ == '__main__': unittest.main()
