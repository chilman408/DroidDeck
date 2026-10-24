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
spec = importlib.util.spec_from_file_location('user_install_package', HERE / 'machine-gpu-package.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class UserInstallBoundary(unittest.TestCase):
    def test_root_linux_closed_account_and_stage_before_reads(self):
        self.assertTrue(callable(getattr(P, 'install_user_sources', None)))
        with mock.patch.object(P, '_source_account', side_effect=AssertionError('no account reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.install_user_sources('runtime', '/home/runtime/payload', 'candidate')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for user, stage in (('root', 'candidate'), ('../user', 'candidate'), ('runtime', '../slot'), ('runtime', 'UPPER')):
                    with self.assertRaises(ValueError):
                        P.install_user_sources(user, '/home/runtime/payload', stage)

    def test_exact_command_no_force_or_destination_override(self):
        self.assertTrue(callable(getattr(P, 'install_user_sources', None)))
        with mock.patch.object(P, 'install_user_sources', return_value={'sourceFilesInstalled': True}) as install, \
                mock.patch('builtins.print') as output:
            P.main(['--install-user-sources', 'runtime', '/home/runtime/payload', 'candidate'])
            install.assert_called_once_with('runtime', '/home/runtime/payload', 'candidate')
            self.assertTrue(json.loads(output.call_args.args[0])['sourceFilesInstalled'])
            for args in (['--install-user-sources'], ['--install-user-sources', 'runtime', '/source'],
                         ['--install-user-sources', 'runtime', '/source', 'candidate', '--force']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated user/source metadata fixtures required')
class UserInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-user-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'user'
        self.home.mkdir(mode=0o700)
        self.source = self.home / 'payload'
        self.opt = self.root / 'opt'
        self.opt.mkdir(mode=0o700)
        self.base = self.opt / 'copy-staging'
        self.slot = self.base / 'candidate'
        self.target = self.opt / 'droiddeck'
        subprocess.run([shutil.which('node'), '-e',
            'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(HERE / 'machine-gpu-bundle.js'), str(self.source)], check=True, capture_output=True, timeout=15)
        for path in (self.home, *self.home.rglob('*')):
            os.chown(path, 1000, 1000)
        self.account = ('runtime', 1000, 1000, str(self.home))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(P, 'INSTALL_PARENT', self.opt).start()
        mock.patch.object(P, 'COPY_STAGING_PARENT', self.base).start()
        mock.patch.object(P, '_source_account', side_effect=lambda user: self.account).start()

    def snapshot(self, path, owner=(0, 0)):
        with ExitStack() as stack:
            context = P.hold_source_package(stack, path, owner=owner)
            return context['generations'], {name: context['read'](name) for name in context['files']}

    def install(self):
        return P.install_user_sources('runtime', str(self.source), 'candidate')

    def test_one_phase_prepares_new_slots_copies_sources_and_never_executes_them(self):
        before = self.snapshot(self.source, (1000, 1000))
        with mock.patch('builtins.exec', side_effect=AssertionError('no payload execution')):
            policy = self.install()
        self.assertEqual(self.snapshot(self.source, (1000, 1000)), before)
        self.assertEqual(self.snapshot(self.target)[1], before[1])
        self.assertTrue(policy['sourceFilesInstalled'])
        self.assertTrue(policy['sourceAccountMatched'])
        self.assertTrue(policy['sourceInputRetained'])
        self.assertEqual(policy['installationMethod'], 'user-copy-then-publish')
        self.assertTrue(all(policy[key] is False for key in P.FALSE_FLAGS))
        self.assertNotIn(str(self.root), json.dumps(policy))
        for path in (self.base, self.slot):
            info = path.lstat()
            self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), (0, 0, 0o700))
        self.assertEqual(list(self.slot.iterdir()), [])

    def test_existing_destination_or_slot_including_empty_slot_refuses_before_writes(self):
        for kind in ('directory', 'symlink'):
            self.target.mkdir() if kind == 'directory' else self.target.symlink_to(self.root / 'absent')
            before = self.target.lstat()
            with mock.patch.object(P.os, 'mkdir', side_effect=AssertionError('no mkdir')):
                with self.assertRaises(ValueError):
                    self.install()
            self.assertEqual(self.target.lstat(), before)
            self.target.rmdir() if kind == 'directory' else self.target.unlink()
        self.base.mkdir(mode=0o700)
        self.slot.mkdir(mode=0o700)
        with mock.patch.object(P.os, 'mkdir', side_effect=AssertionError('no reuse')):
            with self.assertRaises(ValueError):
                self.install()

    def test_existing_private_base_preserves_unrelated_stages(self):
        self.base.mkdir(mode=0o700)
        old = self.base / 'older-stage'
        old.mkdir(mode=0o700)
        (old / 'sentinel').write_bytes(b'preserve')
        self.install()
        self.assertEqual((old / 'sentinel').read_bytes(), b'preserve')

    def test_source_or_base_refusal_precedes_directory_creation(self):
        for source in (str(self.home), '/home/foreign/payload', str(self.home) + '//payload'):
            with mock.patch.object(P.os, 'mkdir', side_effect=AssertionError('no mkdir')):
                with self.assertRaises(ValueError):
                    P.install_user_sources('runtime', source, 'candidate')
        self.base.symlink_to(self.home)
        with mock.patch.object(P.os, 'mkdir', side_effect=AssertionError('no alias writes')):
            with self.assertRaises((OSError, ValueError)):
                self.install()
        self.base.unlink()
        (self.source / 'machine-gpu-entry.py').write_bytes(b'generated foreign source')
        with mock.patch.object(P.os, 'mkdir', side_effect=AssertionError('no invalid-source writes')):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse(self.base.exists())

    def test_ambiguous_slot_creation_is_preserved_and_slot_cannot_be_reused(self):
        actual = P.os.mkdir
        def created(name, *args, **kwargs):
            actual(name, *args, **kwargs)
            if str(name) == 'candidate':
                raise OSError('generated ambiguous completed mkdir')
        with mock.patch.object(P.os, 'mkdir', side_effect=created):
            with self.assertRaises(P.SourcePublicationUncertain):
                self.install()
        self.assertTrue(self.slot.is_dir())
        self.assertFalse(self.target.exists())
        with self.assertRaises(ValueError):
            self.install()

    def test_postinstall_account_drift_is_uncertain_and_keeps_output(self):
        actual = P._source_account
        def account(user):
            value = actual(user)
            return (value[0], 1001, 1001, value[3]) if self.target.exists() else value
        with mock.patch.object(P, '_source_account', side_effect=account):
            with self.assertRaises(P.SourcePublicationUncertain):
                self.install()
        self.assertEqual(len(self.snapshot(self.target)[1]), 12)

    def test_cleanup_failure_keeps_installed_output_and_user_input(self):
        before = self.snapshot(self.source, (1000, 1000))
        actual = P.os.close
        def closed(fd):
            actual(fd)
            if self.target.exists():
                raise OSError('generated descriptor cleanup failure')
        with mock.patch.object(P.os, 'close', side_effect=closed):
            with self.assertRaises(P.SourcePublicationUncertain):
                self.install()
        self.assertEqual(self.snapshot(self.source, (1000, 1000)), before)
        self.assertEqual(len(self.snapshot(self.target)[1]), 12)


if __name__ == '__main__':
    unittest.main()
