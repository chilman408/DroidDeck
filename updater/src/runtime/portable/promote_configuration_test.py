# SPDX-License-Identifier: GPL-3.0-only
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


class ConfigurationPolicyTests(unittest.TestCase):
    def test_cli_has_fixed_etc_destination_and_no_validator_override(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier'), mock.patch.object(PROMOTE, '_install_configuration') as install:
            PROMOTE.main(['--configuration', '/input'])
            self.assertEqual(install.call_args.args[1:], ('/input', Path('/etc')))
            with self.assertRaises(ValueError):
                PROMOTE.main(['--configuration', '/input', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class ConfigurationInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-config-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / 'etc'
        self.parent.mkdir(mode=0o755)
        self.source = self.root / 'input.json'
        self.source.write_bytes(b'generated input passed only to fixture validator')
        self.value = {'config': {'version': 1, 'user': 'fixture', 'uid': 1000, 'gid': 1000,
                                'home': '/home/fixture', 'lanAddress': '192.168.2.215',
                                'port': 8443, 'hostname': 'device.teslaandroid.com'},
                      'configurationDigest': 'a' * 64}

    def install(self, validate=None):
        return PROMOTE._install_configuration(self.copier, str(self.source), self.parent,
                                             validate=validate or (lambda raw: self.value))

    def test_new_normalized_metadata_modes_and_no_overwrite(self):
        old = os.umask(0o077)
        try:
            receipt = self.install()
        finally:
            os.umask(old)
        target = self.parent / 'droiddeck/runtime.json'
        self.assertEqual(json.loads(target.read_bytes()), self.value['config'])
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse(receipt['accountValidated'])
        self.assertFalse(receipt['servicesActivated'])
        with self.assertRaises(ValueError):
            self.install()

    def test_validation_failure_has_no_output(self):
        def rejected(raw):
            raise ValueError('generated rejection')
        with self.assertRaises(ValueError):
            self.install(rejected)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_source_mutation_during_validation_rejects(self):
        def changed(raw):
            self.source.write_bytes(b'changed input')
            return self.value
        with self.assertRaises(ValueError):
            self.install(changed)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_source_link_rejects(self):
        other = self.root / 'other'
        self.source.rename(other)
        self.source.symlink_to(other)
        with self.assertRaises(ValueError):
            self.install()

    def test_raced_empty_target_not_replaced(self):
        rename = PROMOTE.rename_exclusive
        def raced(*args):
            (self.parent / 'droiddeck').mkdir()
            return rename(*args)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced), self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(list((self.parent / 'droiddeck').iterdir()), [])

    def test_postpublish_sync_failure_retains_metadata(self):
        sync = PROMOTE.os.fsync
        def failed(fd):
            if (self.parent / 'droiddeck').exists():
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(PROMOTE.os, 'fsync', side_effect=failed), self.assertRaises(PROMOTE.PromotionUncertain):
            self.install()
        self.assertTrue((self.parent / 'droiddeck/runtime.json').exists())


if __name__ == '__main__':
    unittest.main()
