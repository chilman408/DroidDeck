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
spec = importlib.util.spec_from_file_location('copy_install_package', HERE / 'machine-gpu-package.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class CopyBoundary(unittest.TestCase):
    def test_closed_root_linux_stage_before_reads(self):
        self.assertTrue(callable(getattr(P, 'copy_staged_sources', None)))
        with mock.patch.object(P, '_install_parent', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.copy_staged_sources('candidate')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for stage in ('', '.', '..', 'a/b', 'UPPER', 'a' * 65, None):
                    with self.assertRaises(ValueError):
                        P.copy_staged_sources(stage)

    def test_exact_command_has_no_path_or_force_override(self):
        self.assertTrue(callable(getattr(P, 'copy_staged_sources', None)))
        with mock.patch.object(P, 'copy_staged_sources', return_value={'sourceFilesInstalled': True}) as copied, \
                mock.patch('builtins.print') as output:
            P.main(['--copy-staged-sources', 'candidate'])
            copied.assert_called_once_with('candidate')
            self.assertEqual(json.loads(output.call_args.args[0]), {'sourceFilesInstalled': True})
            for args in (['--copy-staged-sources'], ['--copy-staged-sources', 'slot', '--force'],
                         ['--copy-staged-sources', 'slot', '/other']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated source-copy fixtures required')
class CopyInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-copy-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_slot = self.root / 'source/candidate'
        self.copy_slot = self.root / 'opt/staging/candidate'
        for path in (self.source_slot, self.copy_slot):
            path.mkdir(mode=0o700, parents=True)
        for parent, _, _ in os.walk(self.root):
            Path(parent).chmod(0o700)
        self.source = self.source_slot / 'package'
        self.opt = self.root / 'opt'
        self.target = self.opt / 'droiddeck'
        subprocess.run([shutil.which('node'), '-e',
            'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(HERE / 'machine-gpu-bundle.js'), str(self.source)], check=True, capture_output=True, timeout=15)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(P, 'STAGING_PARENT', self.source_slot.parent).start()
        mock.patch.object(P, 'COPY_STAGING_PARENT', self.copy_slot.parent).start()
        mock.patch.object(P, 'INSTALL_PARENT', self.opt).start()

    def snapshot(self, path):
        with ExitStack() as stack:
            package = P.hold_source_package(stack, path)
            return package['generations'], {name: package['read'](name) for name in package['files']}

    def test_new_copy_retains_source_and_original_created_output_generations(self):
        original = self.snapshot(self.source)
        policy = P.copy_staged_sources('candidate')
        self.assertEqual(self.snapshot(self.source), original)
        installed = self.snapshot(self.target)
        self.assertEqual(installed[1], original[1])
        self.assertNotEqual(installed[0], original[0])
        self.assertTrue(policy['sourceFilesInstalled'])
        self.assertTrue(policy['stagedSourceRetained'])
        self.assertEqual(policy['installationMethod'], 'copy-then-publish')
        self.assertTrue(all(policy[key] is False for key in P.FALSE_FLAGS))
        self.assertEqual(list(self.copy_slot.iterdir()), [])
        self.assertNotIn(str(self.root), json.dumps(policy))
        (self.source / 'machine-gpu-entry.py').write_bytes(b'later source mutation')
        self.assertEqual(self.snapshot(self.target), installed)

    def test_existing_target_or_nonempty_copy_slot_is_never_overwritten(self):
        original = self.snapshot(self.source)
        for kind in ('directory', 'symlink'):
            self.target.mkdir() if kind == 'directory' else self.target.symlink_to(self.root / 'absent')
            before = self.target.lstat()
            with mock.patch.object(P, 'publish_source_package', side_effect=AssertionError('no copy')):
                with self.assertRaises(ValueError):
                    P.copy_staged_sources('candidate')
            self.assertEqual(self.target.lstat(), before)
            self.target.rmdir() if kind == 'directory' else self.target.unlink()
        (self.copy_slot / 'orphan').write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            P.copy_staged_sources('candidate')
        self.assertEqual(self.snapshot(self.source), original)

    def test_copy_slot_must_share_actual_destination_mount_before_writing(self):
        with mock.patch.object(P, '_mount_id', side_effect=[10, 20, 21]), \
                mock.patch.object(P, 'publish_source_package', side_effect=AssertionError('no copy')):
            with self.assertRaises(ValueError):
                P.copy_staged_sources('candidate')
        self.assertEqual(list(self.copy_slot.iterdir()), [])

    def test_source_change_after_copy_cannot_publish_an_installation(self):
        actual = P.publish_source_package
        def changed(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.source / 'machine-gpu-entry.py').write_bytes(b'generated foreign source')
            return result
        with mock.patch.object(P, 'publish_source_package', side_effect=changed):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.copy_staged_sources('candidate')
        self.assertFalse(self.target.exists())
        self.assertEqual(len(self.snapshot(self.copy_slot / 'package')[1]), 12)

    def test_partial_copy_is_retained_and_explicit_retry_refuses(self):
        original = self.snapshot(self.source)
        with mock.patch.object(P.os, 'fsync', side_effect=OSError('generated copy flush failure')):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.copy_staged_sources('candidate')
        partial = list(self.copy_slot.iterdir())
        self.assertEqual(len(partial), 1)
        self.assertTrue(partial[0].name.startswith('.source-attempt-'))
        self.assertFalse(any(self.copy_slot.rglob(P.MANIFEST)))
        with self.assertRaises(ValueError):
            P.copy_staged_sources('candidate')
        self.assertEqual(list(self.copy_slot.iterdir()), partial)
        self.assertEqual(self.snapshot(self.source), original)
        self.assertFalse(self.target.exists())

    def test_racing_target_preserves_both_target_and_verified_copy(self):
        original = self.snapshot(self.source)
        actual = P._rename_between
        def raced(*args):
            self.target.mkdir(mode=0o700)
            (self.target / 'sentinel').write_bytes(b'preserve')
            actual(*args)
        with mock.patch.object(P, '_rename_between', side_effect=raced):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.copy_staged_sources('candidate')
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'preserve')
        self.assertEqual(self.snapshot(self.copy_slot / 'package')[1], original[1])
        self.assertEqual(self.snapshot(self.source), original)

    def test_postmove_flush_failure_preserves_installed_copy_and_source(self):
        original = self.snapshot(self.source)
        actual = P.os.fsync
        def flush(fd):
            if self.target.exists():
                raise OSError('generated installed parent flush failure')
            actual(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=flush):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.copy_staged_sources('candidate')
        self.assertEqual(self.snapshot(self.target)[1], original[1])
        self.assertEqual(self.snapshot(self.source), original)

    def test_same_byte_output_substitution_after_move_cannot_be_adopted(self):
        actual = P._rename_between
        def replaced(*args):
            actual(*args)
            displaced = self.opt / 'displaced'
            self.target.rename(displaced)
            shutil.copytree(displaced, self.target)
        with mock.patch.object(P, '_rename_between', side_effect=replaced):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.copy_staged_sources('candidate')
        self.assertTrue(self.target.is_dir())
        self.assertTrue((self.opt / 'displaced').is_dir())

    def test_cleanup_failure_cannot_mask_successful_or_partial_copy_uncertainty(self):
        actual = P.publish_source_package
        for partial in (False, True):
            def publish(stack, *args, **kwargs):
                result = actual(stack, *args, **kwargs)
                def fail():
                    raise OSError('generated descriptor cleanup failure')
                stack.callback(fail)
                if partial:
                    raise P.SourcePublicationUncertain('generated completed copy reported uncertain')
                return result
            with mock.patch.object(P, 'publish_source_package', side_effect=publish):
                with self.assertRaises(P.SourcePublicationUncertain):
                    P.copy_staged_sources('candidate')
            if not partial:
                self.target.rename(self.opt / 'completed')


if __name__ == '__main__':
    unittest.main()
