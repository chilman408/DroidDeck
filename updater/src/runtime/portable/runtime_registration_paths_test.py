# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-registration-paths.py')
spec = importlib.util.spec_from_file_location('registration_paths_tests', FILE)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


class Boundary(unittest.TestCase):
    def test_fixed_nine_paths_five_dropins_and_closed_redacted_cli(self):
        self.assertEqual(len(R.TARGETS), 9)
        self.assertEqual(set(map(str, R.TARGETS)), set(R.K.REGISTRATIONS.values()))
        self.assertEqual(len(R.DROPINS), 5)
        for args in ([], ['--install'], ['--recover'], ['--inspect-registration-paths', '/foreign'],
                     ['--inspect-final-registration-paths', 'u', '/s', '/r', '-', '--target', '/etc']):
            with self.assertRaises(ValueError):
                R.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Registration path preflight refused; no files or services changed')

    def test_privilege_and_bounds_before_filesystem_or_original_admission(self):
        with ExitStack() as stack, mock.patch.object(R.os, 'open', side_effect=AssertionError('no open')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(R.A.sys, 'platform', platform), mock.patch.object(R.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        R.hold_registration_paths(stack)
            with mock.patch.object(R.A, '_root_linux'):
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        R.hold_final_registration_paths(stack, 'u', '/s', '/r', raw)

    def test_io_errors_are_not_absence(self):
        for error in (PermissionError(), NotADirectoryError(), OSError('generated')):
            with mock.patch.object(R.os, 'stat', side_effect=error), self.assertRaises(OSError):
                R._absent(7, 'name')
        with mock.patch.object(R.os, 'stat', side_effect=FileNotFoundError()):
            R._absent(7, 'name')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated registration fixtures required')
class Namespace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-registration-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.etc = self.root / 'etc'
        for name in ('pam.d', 'tmpfiles.d', 'systemd/system'):
            (self.etc / name).mkdir(parents=True, mode=0o755)
        self.targets = tuple(PurePosixPath(str(self.etc) + str(path).removeprefix('/etc')) for path in R.TARGETS)
        self.dropins = tuple(PurePosixPath(str(self.etc) + str(path).removeprefix('/etc')) for path in R.DROPINS)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(R, 'TARGETS', self.targets).start()
        mock.patch.object(R, 'DROPINS', self.dropins).start()

    def inventory(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_dev, p.lstat().st_ino, p.lstat().st_mode)
                for p in self.root.rglob('*')}

    def test_absent_and_existing_empty_parents_read_only_fd_lifecycle(self):
        import resource
        for path in self.dropins:
            Path(path).mkdir(mode=0o755)
        before = self.inventory()
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            held['check']()
            self.assertIs(held['policy']['registrationPathPreflightVerified'], True)
            self.assertEqual(held['policy']['registrationPathsAbsentObserved'], 9)
            for flag in ('filesWritten', 'servicesRegistered', 'targetReserved', 'targetOwnershipClaimed',
                         'registrationNamespaceVerified', 'effectiveSystemdNamespaceVerified',
                         'installationAuthorized', 'recoveryAuthorized', 'readyForActivation'):
                self.assertIs(held['policy'][flag], False)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_missing_parents_are_not_created_and_later_appearance_refuses(self):
        before = self.inventory()
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            self.assertEqual(self.inventory(), before)
            Path(self.dropins[0]).mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()

    def test_each_existing_target_kind_refuses_before_original_admission(self):
        for index, path in enumerate(self.targets):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            for kind in ('file', 'directory', 'symlink', 'fifo'):
                with self.subTest(path=index, kind=kind):
                    if kind == 'file':
                        path.write_bytes(b'generated foreign')
                    elif kind == 'directory':
                        path.mkdir()
                    elif kind == 'symlink':
                        path.symlink_to('missing')
                    else:
                        os.mkfifo(path)
                    before = self.inventory()
                    with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', side_effect=AssertionError('no source')):
                        with self.assertRaises(ValueError):
                            R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')
                    self.assertEqual(self.inventory(), before)
                    path.rmdir() if kind == 'directory' else path.unlink()

    def test_foreign_dropin_regular_symlink_or_subdirectory_refuses(self):
        for parent in self.dropins:
            parent = Path(parent)
            parent.mkdir(exist_ok=True)
            foreign = parent / 'foreign.conf'
            for kind in ('file', 'symlink', 'directory'):
                with self.subTest(parent=parent.name, kind=kind):
                    if kind == 'file':
                        foreign.write_bytes(b'generated')
                    elif kind == 'symlink':
                        foreign.symlink_to('missing')
                    else:
                        foreign.mkdir()
                    with ExitStack() as stack, self.assertRaises(ValueError):
                        R.hold_registration_paths(stack)
                    foreign.rmdir() if kind == 'directory' else foreign.unlink()

    def test_parent_symlink_nonroot_and_writable_modes_refuse(self):
        path = self.etc / 'pam.d'
        for mode in (0o777, 0o775):
            path.chmod(mode)
            with ExitStack() as stack, self.assertRaises(ValueError):
                R.hold_registration_paths(stack)
        path.chmod(0o755)
        os.chown(path, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            R.hold_registration_paths(stack)
        os.chown(path, 0, 0)
        path.rmdir()
        path.symlink_to('tmpfiles.d')
        with ExitStack() as stack, self.assertRaises(OSError):
            R.hold_registration_paths(stack)

    def test_parent_replacement_and_mode_change_invalidate_hold(self):
        path = self.etc / 'pam.d'
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            path.chmod(0o700)
            with self.assertRaises(ValueError):
                held['check']()
        path.chmod(0o755)
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            path.rename(path.with_name('old-pam'))
            path.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()

    def test_target_or_foreign_dropin_appearing_invalidates_hold(self):
        path = Path(self.targets[0])
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            path.write_bytes(b'generated')
            with self.assertRaises(ValueError):
                held['check']()
        path.unlink()
        parent = Path(self.dropins[0])
        parent.mkdir(mode=0o755)
        with ExitStack() as stack:
            held = R.hold_registration_paths(stack)
            (parent / 'new.conf').write_bytes(b'generated')
            with self.assertRaises(ValueError):
                held['check']()

    def final_fixture(self):
        value = {'registrationTargets': list(map(str, self.targets)),
                 'files': [{'path': str(p), 'registration': True} for p in self.targets]}
        raw = R.K.E._encode(value, compact=True)
        held = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
                'read_file': mock.Mock(return_value=b'generated'), 'policy': {'finalLayoutVerified': True}}
        return held

    def test_one_fresh_authority_all_reads_keep_both_guards(self):
        held = self.final_fixture()
        with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', return_value=held) as admit:
            result = R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')
            admit.assert_called_once_with(stack, 'u', '/s', '/r', b'{}')
            self.assertEqual(result['read_plan'](), held['read_plan']())
            self.assertEqual(result['read_file'](str(self.targets[0])), b'generated')
            held['read_file'].assert_called_once_with(str(self.targets[0]))
            Path(self.targets[0]).write_bytes(b'generated')
            with self.assertRaises(ValueError):
                result['read_file'](str(self.targets[1]))
            self.assertEqual(held['read_file'].call_count, 1)

    def test_plan_mismatch_and_source_drift_refuse(self):
        held = self.final_fixture()
        held['read_plan'].return_value = b'{"registrationTargets":[]}'
        with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', return_value=held), self.assertRaises(ValueError):
            R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')
        held = self.final_fixture()
        with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', return_value=held):
            result = R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')
            held['read_plan'].return_value = b'changed'
            with self.assertRaises(ValueError):
                result['read_plan']()

    def test_namespace_drift_during_source_admission_refuses(self):
        held = self.final_fixture()
        def admit(*args):
            Path(self.targets[0]).write_bytes(b'generated')
            return held
        with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', side_effect=admit), self.assertRaises(ValueError):
            R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')

    def test_namespace_drift_during_payload_read_refuses_after_read(self):
        held = self.final_fixture()
        def read(_):
            Path(self.targets[0]).write_bytes(b'generated')
            return b'generated'
        held['read_file'].side_effect = read
        with ExitStack() as stack, mock.patch.object(R.K, 'hold_final_install_layout', return_value=held):
            result = R.hold_final_registration_paths(stack, 'u', '/s', '/r', b'{}')
            with self.assertRaises(ValueError):
                result['read_file'](str(self.targets[0]))

    def test_failure_closes_descriptors_without_changing_limits(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        Path(self.targets[0]).write_bytes(b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            R.hold_registration_paths(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)
        Path(self.targets[0]).unlink()
        original = R.os.open
        def limited(name, *args, **kwargs):
            if name == 'pam.d':
                import errno
                raise OSError(errno.EMFILE, 'generated descriptor exhaustion')
            return original(name, *args, **kwargs)
        with ExitStack() as stack, mock.patch.object(R.os, 'open', side_effect=limited), self.assertRaises(OSError):
            R.hold_registration_paths(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)


if __name__ == '__main__':
    unittest.main()
