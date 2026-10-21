# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)


class ArtifactLayoutPolicyTests(unittest.TestCase):
    def test_fixed_layout_covers_every_artifact(self):
        self.assertTrue(hasattr(PROMOTE, 'ARTIFACT_LAYOUT'), 'Artifact promotion not implemented')
        self.assertEqual(PROMOTE.ARTIFACT_PARENT, Path('/opt'))
        self.assertEqual(PROMOTE.ARTIFACT_TARGET, 'droiddeck')
        self.assertEqual(PROMOTE.ARTIFACT_LAYOUT['scrcpy-server-v4.0'], ('guest/scrcpy-server-v4.0.jar', 0o600))
        self.assertEqual(PROMOTE.ARTIFACT_LAYOUT['libgbinder.so.1.1.45'], ('libgbinder/libgbinder.so.1.1.45', 0o644))

    def test_only_fixed_artifact_cli_dispatch(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier', return_value=mock.Mock()), mock.patch.object(PROMOTE, '_promote_artifacts', create=True) as installer:
            PROMOTE.main(['--artifacts', 'fresh-stage'])
            self.assertEqual(installer.call_count, 1)
            for args in (['--artifacts'], ['--artifacts', 'stage', '/override'], ['--artifacts', '../stage']):
                with self.assertRaises(ValueError):
                    PROMOTE.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux fixtures required')
class ArtifactLayoutTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-layout-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.input, self.stages, self.parent = [self.base / n for n in ('input', 'stages', 'opt')]
        for p in (self.input, self.stages, self.parent):
            p.mkdir(mode=0o700)
        self.pins = {}
        for name in self.copier.ARTIFACT_PINS:
            data = ('generated layout fixture: ' + name).encode()
            (self.input / name).write_bytes(data)
            self.pins[name] = (len(data), hashlib.sha256(data).hexdigest())
        self.copier._stage_images(str(self.input), self.stages, 'seed', self.pins, artifacts=True)

    def install(self):
        return PROMOTE._promote_artifacts(self.copier, self.stages, self.parent, 'seed', self.pins)

    def test_atomic_layout_modes_links_and_no_activation(self):
        self.assertFalse((self.parent / 'droiddeck').exists())
        receipt = self.install()
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['readyForActivation'])
        self.assertFalse(receipt['buildPinsMatched'])
        root = self.parent / 'droiddeck'
        self.assertEqual(json.loads((root / 'artifact-layout.json').read_text()), receipt)
        for name, (relative, mode) in PROMOTE.ARTIFACT_LAYOUT.items():
            p = root / relative
            info = p.stat()
            self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, mode, 1))
            self.assertEqual(p.read_bytes(), (self.input / name).read_bytes())
        for name, target in PROMOTE.ARTIFACT_LINKS.items():
            self.assertEqual(os.readlink(root / 'libgbinder' / name), target)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((root / 'guest').stat().st_mode), 0o700)
        with self.assertRaises(ValueError):
            self.install()

    def test_existing_and_raced_empty_destination_refuse(self):
        (self.parent / 'droiddeck').mkdir()
        with self.assertRaises(ValueError):
            self.install()
        (self.parent / 'droiddeck').rmdir()
        rename = PROMOTE.rename_exclusive
        def raced(*args, **kwargs):
            (self.parent / 'droiddeck').mkdir()
            return rename(*args, **kwargs)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced):
            with self.assertRaises(FileExistsError):
                self.install()
        self.assertEqual(list((self.parent / 'droiddeck').iterdir()), [])

    def test_source_mutation_and_unsafe_layout_reject(self):
        source = self.stages / 'seed'
        (source / next(iter(self.pins))).write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.parent / 'droiddeck').exists())

    def test_corrupted_candidate_cannot_publish(self):
        verify = PROMOTE.verify_artifact_layout
        def corrupt(copier, directory, pins):
            (directory / 'guest' / 'logical-capture.jar').write_bytes(b'bad')
            return verify(copier, directory, pins)
        with mock.patch.object(PROMOTE, 'verify_artifact_layout', side_effect=corrupt):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse((self.parent / 'droiddeck').exists())

    def test_prepublication_sync_failure_has_no_final_root(self):
        with mock.patch.object(PROMOTE.os, 'fsync', side_effect=OSError('generated disk failure')):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse((self.parent / 'droiddeck').exists())

    def test_postrename_sync_failure_is_uncertain_not_rollback(self):
        fsync = PROMOTE.os.fsync
        def failed(fd):
            if (self.parent / 'droiddeck').exists():
                raise OSError('generated final sync failure')
            return fsync(fd)
        with mock.patch.object(PROMOTE.os, 'fsync', side_effect=failed):
            with self.assertRaises(PROMOTE.PromotionUncertain):
                self.install()
        self.assertTrue((self.parent / 'droiddeck' / 'artifact-layout.json').exists())


if __name__ == '__main__':
    unittest.main()
