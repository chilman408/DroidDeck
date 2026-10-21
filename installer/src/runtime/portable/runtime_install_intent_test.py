# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-install-intent.py')
spec = importlib.util.spec_from_file_location('install_intent_tests', FILE)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class Boundary(unittest.TestCase):
    def test_fixed_paths_and_closed_cli_redact_recovery_install_overrides(self):
        self.assertEqual(tuple(map(str, H.TARGETS)), ('/opt/droiddeck', '/etc/droiddeck'))
        self.assertEqual(str(H.JOURNAL), '/var/lib/droiddeck-install-intent')
        for args in ([], ['--install'], ['--recover'], ['--prepare-new-install-intent', 'u', '/s', '/r', '/p', '/metadata'],
                     ['--prepare-new-install-intent', 'u', '/s', '/r', '/p', '-', '--target', '/foreign']):
            with self.assertRaises(ValueError):
                H.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Installation intent refused; no installation or recovery performed')

    def test_privilege_bounded_settings_and_disjoint_names_before_parent_access(self):
        with ExitStack() as stack, mock.patch.object(H, '_hold_parent', side_effect=AssertionError('no parent')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(H.A.sys, 'platform', platform), mock.patch.object(H.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        H.prepare_install_intent(stack, 'u', '/root/s', '/root/r', '/root/p', b'{}')
            with mock.patch.object(H.A, '_root_linux'):
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        H.prepare_install_intent(stack, 'u', '/root/s', '/root/r', '/root/p', raw)
                for path in ('/opt/droiddeck/source', '/etc/droiddeck', '/var/lib/droiddeck-install-intent', '/var/lib'):
                    with self.assertRaises(ValueError):
                        H.prepare_install_intent(stack, 'u', path, '/root/r', '/root/p', b'{}')

    def test_absence_errors_are_not_absence_and_dangling_name_is_present(self):
        with mock.patch.object(H.os, 'stat', side_effect=PermissionError('generated')):
            with self.assertRaises(PermissionError):
                H._absent(7, 'droiddeck')
        with mock.patch.object(H.os, 'stat', side_effect=FileNotFoundError()):
            H._absent(7, 'droiddeck')
        with mock.patch.object(H.os, 'stat', return_value=object()):
            with self.assertRaises(ValueError):
                H._absent(7, 'droiddeck')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated installation intent fixtures required')
class Intent(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-intent-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opt, self.etc, self.var = (self.root / name for name in ('opt', 'etc', 'var'))
        for path in (self.opt, self.etc, self.var):
            path.mkdir(mode=0o700)
        self.journal = self.var / 'droiddeck-install-intent'
        self.targets = tuple(PurePosixPath(str(path / 'droiddeck')) for path in (self.opt, self.etc))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(H, 'TARGETS', self.targets).start()
        mock.patch.object(H, 'JOURNAL', PurePosixPath(str(self.journal))).start()
        self.held = {'check': mock.Mock(), 'read_plan': lambda: b'generated independently admitted plan\n',
            'policy': {**dict.fromkeys(H.E.P.C.L.FALSE_FLAGS, False), 'rootPrivateCopyVerified': False,
                       'sourceRecipeCodeExecuted': True, 'candidateManifestSha256': 'a' * 64,
                       'newOwnedInstallCandidatePublished': False, 'recipeFilesWritten': False}}
        self.gate = mock.patch.object(H.G, 'hold_owned_install_candidate', return_value=self.held).start()

    def prepare(self, stack):
        return H.prepare_install_intent(stack, 'fixture', '/root/source', '/root/recipes', '/root/candidate', b'{}')

    def test_exclusive_durable_record_exact_bytes_permissions_no_target_writes(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        original_fsync = H.os.fsync
        syncs = []
        def sync(fd):
            syncs.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            original_fsync(fd)
        with ExitStack() as stack, mock.patch.object(H.os, 'fsync', side_effect=sync):
            result = self.prepare(stack)
            self.gate.assert_called_once()
            raw = result['read_intent']()
            value = json.loads(raw)
            self.assertEqual(value['phase'], 'prepared')
            self.assertRegex(value['transaction'], '^[a-f0-9]{32}$')
            self.assertEqual(value['planSha256'], H.E._sha(self.held['read_plan']()))
            self.assertEqual(value['candidateManifestSha256'], 'a' * 64)
            self.assertEqual([row['path'] for row in value['targets']], list(map(str, self.targets)))
            self.assertEqual(value['targets'][0]['parentInode'], self.opt.stat().st_ino)
            self.assertEqual((self.journal / 'intent.json').read_bytes(), raw)
            self.assertEqual(syncs, [False, True, True])
            self.assertEqual([p.name for p in self.journal.iterdir()], ['intent.json'])
            for path, mode in ((self.journal, 0o700), (self.journal / 'intent.json', 0o600)):
                info = path.lstat()
                self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, 0, mode))
            self.assertIs(result['policy']['intentDurabilityVerified'], True)
            for flag in ('targetReserved', 'targetOwnershipClaimed', 'installationAuthorized', 'recoveryAuthorized',
                         'installable', 'readyForActivation', 'servicesActivated', 'rootPrivateCopyVerified',
                         'newOwnedInstallCandidatePublished', 'recipeFilesWritten'):
                self.assertIs(result['policy'][flag], False)
            self.assertEqual(list(self.opt.iterdir()), [])
            self.assertEqual(list(self.etc.iterdir()), [])
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_existing_target_directory_dangling_symlink_file_fifo_refuse_before_candidate(self):
        for parent in (self.opt, self.etc):
            target = parent / 'droiddeck'
            for kind in ('directory', 'symlink', 'file', 'fifo'):
                if kind == 'directory':
                    target.mkdir(mode=0o700)
                elif kind == 'symlink':
                    target.symlink_to('/nonexistent-generated-test-only')
                elif kind == 'file':
                    target.write_bytes(b'generated foreign target')
                else:
                    os.mkfifo(target, 0o600)
                before = H.A._identity(target.lstat())
                with ExitStack() as stack, self.assertRaises(ValueError):
                    self.prepare(stack)
                self.assertEqual(H.A._identity(target.lstat()), before)
                self.assertFalse(self.journal.exists())
                self.gate.assert_not_called()
                target.rmdir() if kind == 'directory' else target.unlink()

    def test_existing_intent_of_any_kind_refuses_without_adoption(self):
        for kind in ('empty', 'partial', 'symlink', 'file'):
            if kind in ('empty', 'partial'):
                self.journal.mkdir(mode=0o700)
                if kind == 'partial':
                    (self.journal / 'intent.json').write_bytes(b'partial')
            elif kind == 'symlink':
                self.journal.symlink_to('/nonexistent-generated-test-only')
            else:
                self.journal.write_bytes(b'generated foreign file')
            before = H.A._identity(self.journal.lstat())
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.prepare(stack)
            self.assertEqual(H.A._identity(self.journal.lstat()), before)
            self.gate.assert_not_called()
            if kind in ('empty', 'partial'):
                if kind == 'partial':
                    (self.journal / 'intent.json').unlink()
                self.journal.rmdir()
            else:
                self.journal.unlink()

    def test_source_refusal_and_target_appearing_during_admission_create_no_intent(self):
        self.gate.side_effect = ValueError('generated candidate refusal')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertFalse(self.journal.exists())
        def raced(*args):
            (self.etc / 'droiddeck').mkdir(mode=0o700)
            return self.held
        self.gate.side_effect = raced
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertFalse(self.journal.exists())
        self.assertTrue((self.etc / 'droiddeck').is_dir())

    def test_foreign_journal_racing_exclusive_mkdir_is_never_overwritten(self):
        original = H.os.mkdir
        def raced(name, mode=0o777, *, dir_fd=None):
            original(name, mode, dir_fd=dir_fd)
            (self.journal / 'foreign').write_bytes(b'generated foreign marker')
            return original(name, mode, dir_fd=dir_fd)
        with mock.patch.object(H.os, 'mkdir', side_effect=raced), ExitStack() as stack, self.assertRaises(FileExistsError):
            self.prepare(stack)
        self.assertEqual((self.journal / 'foreign').read_bytes(), b'generated foreign marker')
        self.assertFalse((self.journal / 'intent.json').exists())

    def test_failed_partial_write_retained_and_retry_refused(self):
        def partial(stack, fd, name, raw):
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
            try:
                os.write(target, b'partial')
            finally:
                os.close(target)
            raise OSError('generated partial write')
        with mock.patch.object(H.S, '_write_private', side_effect=partial), ExitStack() as stack, self.assertRaises(H.InstallIntentUncertain):
            self.prepare(stack)
        self.assertEqual((self.journal / 'intent.json').read_bytes(), b'partial')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertEqual((self.journal / 'intent.json').read_bytes(), b'partial')

    def test_directory_fsync_failure_retains_complete_record_without_success(self):
        original = H.os.fsync
        def fail(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('generated directory durability failure')
            original(fd)
        with mock.patch.object(H.os, 'fsync', side_effect=fail), ExitStack() as stack, self.assertRaises(H.InstallIntentUncertain):
            self.prepare(stack)
        self.assertEqual(json.loads((self.journal / 'intent.json').read_bytes())['phase'], 'prepared')

    def test_source_drift_after_creation_preserves_intent_and_foreign_target(self):
        original = H.S._write_private
        def drift(*args):
            result = original(*args)
            self.held['check'].side_effect = ValueError('generated account or source drift')
            (self.opt / 'droiddeck').mkdir(mode=0o700)
            return result
        with mock.patch.object(H.S, '_write_private', side_effect=drift), ExitStack() as stack, self.assertRaises(H.InstallIntentUncertain):
            self.prepare(stack)
        self.assertTrue((self.journal / 'intent.json').is_file())
        self.assertTrue((self.opt / 'droiddeck').is_dir())

    def test_retained_reads_detect_same_byte_inode_replacement_and_extra_inventory(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            extra = self.journal / 'foreign'
            extra.write_bytes(b'generated')
            with self.assertRaises(H.InstallIntentUncertain):
                result['read_intent']()
            extra.unlink()
            path = self.journal / 'intent.json'
            raw = path.read_bytes()
            path.rename(self.journal / 'old')
            path.write_bytes(raw)
            path.chmod(0o600)
            (self.journal / 'old').unlink()
            with self.assertRaises(H.InstallIntentUncertain):
                result['check']()

    def test_retained_reads_detect_target_appearance_and_source_drift(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            target = self.etc / 'droiddeck'
            target.mkdir(mode=0o700)
            with self.assertRaises(H.InstallIntentUncertain):
                result['read_intent']()
            target.rmdir()
            self.held['check'].side_effect = ValueError('generated source drift')
            with self.assertRaises(H.InstallIntentUncertain):
                result['check']()

    def test_parent_modes_symlink_and_replacement_refuse_without_foreign_cleanup(self):
        self.opt.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.opt.chmod(0o700)
        old = self.opt.with_name('original')
        self.opt.rename(old)
        self.opt.symlink_to(old)
        with ExitStack() as stack, self.assertRaises(OSError):
            self.prepare(stack)
        self.opt.unlink()
        old.rename(self.opt)
        with ExitStack() as stack:
            result = self.prepare(stack)
            self.var.rename(self.var.with_name('original-var'))
            self.var.mkdir(mode=0o700)
            with self.assertRaises(H.InstallIntentUncertain):
                result['check']()
        self.assertEqual(list(self.var.iterdir()), [])

    def test_failure_descriptor_cleanup_and_limit_unchanged(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with mock.patch.object(H.S, '_write_private', side_effect=OSError('generated write refusal')), ExitStack() as stack, self.assertRaises(H.InstallIntentUncertain):
            self.prepare(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)
        self.assertTrue(self.journal.is_dir())


if __name__ == '__main__':
    unittest.main()
