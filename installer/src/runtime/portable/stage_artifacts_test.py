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

SPEC = importlib.util.spec_from_file_location('stage', Path(__file__).with_name('stage-images.py'))
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class ArtifactPolicyTests(unittest.TestCase):
    def test_fixed_eight_artifacts_and_no_activation(self):
        self.assertEqual(STAGE.ARTIFACT_PARENT, Path('/var/lib/droiddeck/artifact-staging'))
        self.assertEqual(len(STAGE.ARTIFACT_PINS), 8)
        self.assertEqual(STAGE.ARTIFACT_PINS['scrcpy-server-v4.0'],
                         (732226, '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a'))
        for name, (size, digest) in STAGE.ARTIFACT_PINS.items():
            self.assertEqual(Path(name).name, name)
            self.assertGreater(size, 0)
            self.assertEqual(len(digest), 64)

    def test_cli_uses_fixed_policy_only(self):
        with mock.patch.object(STAGE.sys, 'platform', 'linux'), mock.patch.object(STAGE.os, 'geteuid', return_value=0, create=True), mock.patch.object(STAGE, '_stage_images') as copier:
            STAGE.main(['--artifacts', '/private/source', 'fresh'])
            copier.assert_called_once_with('/private/source', STAGE.ARTIFACT_PARENT,
                                           'fresh', STAGE.ARTIFACT_PINS, artifacts=True)
            for args in (['--artifacts'], ['--artifacts', '/source'],
                         ['--artifacts', '/source', 'fresh', '/override']):
                with self.assertRaises(ValueError):
                    STAGE.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux fixtures required')
class ArtifactStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-artifact-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source, self.parent = self.base / 'source', self.base / 'target'
        self.source.mkdir(mode=0o700)
        self.parent.mkdir(mode=0o700)
        self.pins = {}
        for name in STAGE.ARTIFACT_PINS:
            data = ('generated fixture ' + name).encode()
            (self.source / name).write_bytes(data)
            self.pins[name] = (len(data), hashlib.sha256(data).hexdigest())

    def stage(self, name='new'):
        return STAGE._stage_images(str(self.source), self.parent, name, self.pins, artifacts=True)

    def test_private_copies_and_honest_receipt(self):
        receipt = self.stage()
        target = self.parent / 'new'
        self.assertEqual(receipt['kind'], 'protected-runtime-artifact-stage')
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['buildPinsMatched'])
        self.assertFalse(receipt['readyForActivation'])
        self.assertNotIn('readyForInitialization', receipt)
        self.assertEqual(json.loads((target / 'ready.json').read_text()), receipt)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        for name in [*self.pins, 'ready.json']:
            info = (target / name).stat()
            self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)),
                             (0, 0, 1, 0o600))
        for name in self.pins:
            self.assertEqual((target / name).read_bytes(), (self.source / name).read_bytes())
            self.assertNotEqual((target / name).stat().st_ino, (self.source / name).stat().st_ino)
        with self.assertRaises(FileExistsError):
            self.stage()

    def test_link_and_wrong_bytes_reject_before_output(self):
        name = next(iter(self.pins))
        (self.source / name).write_bytes(b'wrong')
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse((self.parent / 'new').exists())
        (self.source / name).unlink()
        (self.source / name).symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse((self.parent / 'new').exists())

    def test_durability_failure_has_no_ready_receipt(self):
        with mock.patch.object(STAGE.os, 'fsync', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                self.stage()
        self.assertFalse((self.parent / 'new' / 'ready.json').exists())

    def test_changed_source_during_copy_has_no_ready_receipt(self):
        copy = STAGE.copy_image
        def changed(*args):
            result = copy(*args)
            (self.source / next(iter(self.pins))).write_bytes(b'mutated')
            return result
        with mock.patch.object(STAGE, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.stage()
        self.assertFalse((self.parent / 'new' / 'ready.json').exists())


if __name__ == '__main__':
    unittest.main()
