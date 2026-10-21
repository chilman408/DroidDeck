# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
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
spec = importlib.util.spec_from_file_location('source_install_package', HERE / 'machine-gpu-package.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class InstallBoundary(unittest.TestCase):
    def test_exact_dispatch(self):
        with mock.patch.object(P, 'install_staged_sources', return_value={'sourceFilesInstalled': True}) as install, \
                mock.patch('builtins.print') as output:
            P.main(['--install-staged-sources', 'candidate'])
            install.assert_called_once_with('candidate')
            self.assertEqual(json.loads(output.call_args.args[0]), {'sourceFilesInstalled': True})

    def test_closed_root_linux_command_before_reads(self):
        self.assertTrue(callable(getattr(P, 'install_staged_sources', None)))
        with mock.patch.object(P, 'hold_source_package', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.install_staged_sources('candidate')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for stage in ('', '.', '..', 'a/b', 'UPPER', 'a' * 65, None):
                    with self.assertRaises(ValueError):
                        P.install_staged_sources(stage)
            for args in (['--install-staged-sources'], ['--install-staged-sources', 'slot', '--force'],
                         ['--install-staged-sources', 'slot', '/other']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated fixtures required')
class InstallSources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-source-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staging = self.root / 'staging'
        self.slot = self.staging / 'candidate'
        self.slot.mkdir(mode=0o700, parents=True)
        self.staging.chmod(0o700)
        self.source = self.slot / 'package'
        self.opt = self.root / 'opt'
        self.opt.mkdir(mode=0o755)
        self.target = self.opt / 'droiddeck'
        subprocess.run([shutil.which('node'), '-e',
            'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(HERE / 'machine-gpu-bundle.js'), str(self.source)], check=True, capture_output=True, timeout=15)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(P, 'STAGING_PARENT', self.staging).start()
        mock.patch.object(P, 'INSTALL_PARENT', self.opt).start()

    def test_actual_atomic_install_preserves_generations_and_closed_policy(self):
        with ExitStack() as stack:
            before = P.hold_source_package(stack, self.source)['generations']
        other = self.opt / 'unrelated'
        other.write_bytes(b'preserve unrelated')
        policy = P.install_staged_sources('candidate')
        self.assertTrue(policy['sourceFilesInstalled'])
        self.assertTrue(policy['newDirectoryOnly'])
        self.assertTrue(all(policy[key] is False for key in P.FALSE_FLAGS))
        self.assertNotIn(str(self.root), json.dumps(policy))
        self.assertFalse(self.source.exists())
        self.assertEqual(list(self.slot.iterdir()), [])
        self.assertEqual(other.read_bytes(), b'preserve unrelated')
        with ExitStack() as stack:
            installed = P.hold_source_package(stack, self.target)
            self.assertEqual(installed['generations'], before)
        with mock.patch.object(P, '_rename_between', side_effect=AssertionError('no retry write')):
            with self.assertRaises(ValueError):
                P.install_staged_sources('candidate')

    def test_existing_target_including_dangling_symlink_never_replaced(self):
        for kind in ('directory', 'file', 'symlink'):
            if kind == 'directory':
                self.target.mkdir()
            elif kind == 'file':
                self.target.write_bytes(b'existing')
            else:
                self.target.symlink_to(self.root / 'absent')
            original = self.target.lstat()
            with mock.patch.object(P, '_rename_between', side_effect=AssertionError('no write')):
                with self.assertRaises(ValueError):
                    P.install_staged_sources('candidate')
            self.assertEqual(self.target.lstat(), original)
            self.assertTrue(self.source.is_dir())
            self.target.rmdir() if kind == 'directory' else self.target.unlink()

    def test_bad_source_slot_or_destination_ancestor_refuses_before_move(self):
        extra = self.slot / 'orphan'
        extra.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            P.install_staged_sources('candidate')
        extra.unlink()
        self.opt.chmod(0o777)
        with self.assertRaises(ValueError):
            P.install_staged_sources('candidate')
        self.opt.chmod(0o755)
        source = self.source / 'machine-gpu-entry.py'
        source.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            P.install_staged_sources('candidate')
        self.assertFalse(self.target.exists())

    def test_cross_device_refuses_without_attempt(self):
        actual = P.os.fstat
        parent = P._install_parent
        resolved = False
        def parents(*args, **kwargs):
            nonlocal resolved
            result = parent(*args, **kwargs)
            if args[1] == self.opt:
                resolved = True
            return result
        def changed(fd):
            info = actual(fd)
            if resolved and (info.st_dev, info.st_ino) == (self.opt.stat().st_dev, self.opt.stat().st_ino):
                return type('OtherFilesystem', (), {**{name: getattr(info, name) for name in dir(info) if name.startswith('st_')},
                                                   'st_dev': info.st_dev + 1})()
            return info
        with mock.patch.object(P, '_install_parent', side_effect=parents), mock.patch.object(P.os, 'fstat', side_effect=changed), \
                mock.patch.object(P, '_rename_between', side_effect=AssertionError('no move')):
            with self.assertRaises(ValueError):
                P.install_staged_sources('candidate')
        self.assertTrue(self.source.is_dir())

    def test_equal_device_but_different_mount_refuses_before_move(self):
        with mock.patch.object(P, '_mount_id', side_effect=[10, 11]), \
                mock.patch.object(P, '_rename_between', side_effect=AssertionError('no move')):
            with self.assertRaises(ValueError):
                P.install_staged_sources('candidate')
        self.assertTrue(self.source.is_dir())
        self.assertFalse(self.target.exists())

    def test_move_or_flush_uncertainty_preserves_result_and_never_retries(self):
        actual = P._rename_between
        def moved(*args):
            actual(*args)
            raise OSError('ambiguous completed move')
        with mock.patch.object(P, '_rename_between', side_effect=moved) as move:
            with self.assertRaises(P.SourcePublicationUncertain):
                P.install_staged_sources('candidate')
            self.assertEqual(move.call_count, 1)
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.source.exists())
        with self.assertRaises(ValueError):
            P.install_staged_sources('candidate')

    def test_postmove_flush_failure_is_uncertain_and_keeps_installed_sources(self):
        actual = P.os.fsync
        def flush(fd):
            if self.target.exists():
                raise OSError('generated postmove flush failure')
            actual(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=flush):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.install_staged_sources('candidate')
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.source.exists())

    def test_premove_flush_failure_keeps_staging_and_is_not_install_uncertainty(self):
        with mock.patch.object(P.os, 'fsync', side_effect=OSError('generated premove flush failure')), \
                mock.patch.object(P, '_rename_between', side_effect=AssertionError('no move')):
            with self.assertRaises(OSError):
                P.install_staged_sources('candidate')
        self.assertTrue(self.source.is_dir())
        self.assertFalse(self.target.exists())

    def test_racing_destination_is_not_replaced(self):
        actual = P._rename_between
        def raced(*args):
            self.target.mkdir(mode=0o700)
            (self.target / 'sentinel').write_bytes(b'preserve')
            actual(*args)
        with mock.patch.object(P, '_rename_between', side_effect=raced) as move:
            with self.assertRaises(P.SourcePublicationUncertain):
                P.install_staged_sources('candidate')
            self.assertEqual(move.call_count, 1)
        self.assertTrue(self.source.is_dir())
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'preserve')

    def test_replaced_same_byte_package_after_move_is_not_adopted(self):
        actual = P._rename_between
        def replaced(*args):
            actual(*args)
            displaced = self.opt / 'displaced'
            self.target.rename(displaced)
            shutil.copytree(displaced, self.target)
        with mock.patch.object(P, '_rename_between', side_effect=replaced):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.install_staged_sources('candidate')
        self.assertTrue(self.target.is_dir())
        self.assertTrue((self.opt / 'displaced').is_dir())

    def test_descriptor_cleanup_cannot_hide_completed_install(self):
        actual = P.os.close
        def close(fd):
            actual(fd)
            if self.target.exists():
                raise OSError('generated cleanup failure')
        with mock.patch.object(P.os, 'close', side_effect=close):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.install_staged_sources('candidate')
        self.assertTrue(self.target.is_dir())


if __name__ == '__main__':
    unittest.main()
