# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-host-package-files.py')
spec = importlib.util.spec_from_file_location('host_package_file_tests', FILE)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def metadata(files):
    data = {name: ('generated ' + pair[0]).encode() for name, pair in files.items()}
    status = ''.join(p + '\t1.2-test\t' + ('all' if p == 'waydroid' else 'amd64') + '\tinstall ok installed\n' for p in H.PACKAGES).encode()
    owners = ''.join(p + ': /' + recorded + '\n' for p, recorded in files.values()).encode()
    sums = {p: ''.join(hashlib.md5(data[name], usedforsecurity=False).hexdigest() + '  ' + recorded + '\n'
            for name, (owner, recorded) in files.items() if owner == p).encode() for p in H.PACKAGES}
    def query(*args):
        if args[0] == '-W':
            return query.status
        if args[0] == '-S':
            return query.owners
        if args[0] == '--control-show':
            return query.sums[args[1]]
        raise AssertionError('closed query')
    query.status, query.owners, query.sums = status, owners, sums
    return data, query


class Boundary(unittest.TestCase):
    def test_exact_host_file_mapping_and_closed_redacted_cli(self):
        self.assertEqual(len(H.FILES), 3)
        self.assertEqual(H.FILES['/usr/lib/systemd/system/waydroid-container.service'], ('waydroid', 'lib/systemd/system/waydroid-container.service'))
        self.assertEqual(str(H.ALIAS), '/lib')
        for args in ([], ['--install'], ['--recover'], ['--inspect-host-package-files', '--root', '/foreign'],
                     ['--inspect-final-host-package-files', 'u', '/s', '/r', '-', '--package', 'foreign']):
            with self.assertRaises(ValueError):
                H.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Host package consistency refused; no files or services changed')

    def test_command_fixed_environment_no_shell_stdin_and_output_bounds(self):
        result = SimpleNamespace(stdout=b'generated', stderr=b'')
        with mock.patch.object(H.subprocess, 'run', return_value=result) as run:
            self.assertEqual(H._query('-W', 'waydroid'), b'generated')
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/usr/bin/dpkg-query', '-W', 'waydroid'])
            self.assertEqual(kwargs['cwd'], '/')
            self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(kwargs['check'])
            self.assertEqual(kwargs['timeout'], 5)
            self.assertNotIn('shell', kwargs)
            for stdout, stderr in ((b'', b''), (b'x' * (H.MAX_QUERY + 1), b''), (b'x', b'warning')):
                result.stdout, result.stderr = stdout, stderr
                with self.assertRaises(ValueError):
                    H._query('-W', 'waydroid')
        with mock.patch.object(H.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed', 5)):
            with self.assertRaises(subprocess.SubprocessError):
                H._query('-W', 'waydroid')

    def test_metadata_status_owner_checksum_closed_schema(self):
        _, query = metadata(H.FILES)
        original_status, original_owners, original_sums = query.status, query.owners, dict(query.sums)
        with mock.patch.object(H, '_query', side_effect=query):
            _, versions, pins = H._metadata()
            self.assertEqual((len(versions), len(pins)), (3, 3))
            with self.assertRaises(TypeError):
                pins['foreign'] = 'bad'
            for status in (b'bad', original_status + original_status.splitlines(keepends=True)[0],
                           original_status.replace(b'install ok installed', b'deinstall ok config-files'),
                           original_status.replace(b'amd64', b'arm64'), original_status.replace(b'1.2-test', b'bad version')):
                query.status = status
                with self.assertRaises(ValueError):
                    H._metadata()
            query.status = original_status
            for owners in (b'bad', original_owners + original_owners, original_owners.replace(b'waydroid: ', b'foreign: '),
                           b'local diversion from: /foreign\n' + original_owners):
                query.owners = owners
                with self.assertRaises(ValueError):
                    H._metadata()
            query.owners = original_owners
            row = original_sums['waydroid']
            for checksum in (b'', b'bad row', row + row, row.replace(b'lib/systemd', b'../lib/systemd'),
                             row.replace(b'lib/systemd', b'/lib/systemd'), row.replace(b'lib/systemd', b'lib//systemd'),
                             row.replace(b'lib/systemd', b'lib\\systemd'), row * 8193):
                query.sums['waydroid'] = checksum
                with self.assertRaises(ValueError):
                    H._metadata()

    def test_privilege_raw_bounds_and_budget_before_queries_or_opens(self):
        with ExitStack() as stack, mock.patch.object(H, '_metadata', side_effect=AssertionError('no query')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(H.A.sys, 'platform', platform), mock.patch.object(H.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        H.hold_host_package_files(stack)
            with mock.patch.object(H.A, '_root_linux'):
                with mock.patch.object(H, '_budget', side_effect=ValueError('generated')), self.assertRaises(ValueError):
                    H.hold_host_package_files(stack)
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        H.hold_final_host_package_files(stack, 'u', '/s', '/r', raw)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated host package fixtures required')
class Observation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-host-packages-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {str(self.root) + path: pair for path, pair in H.FILES.items()}
        self.data, self.query = metadata(self.files)
        for name, data in self.data.items():
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(data)
            path.chmod(0o644)
        self.alias = self.root / 'lib'
        self.alias.symlink_to('usr/lib')
        self.file = Path(next(iter(self.files)))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(H, 'FILES', self.files).start()
        mock.patch.object(H, 'ALIAS', PurePosixPath(self.alias)).start()
        self.queries = mock.patch.object(H, '_query', side_effect=self.query).start()

    def snapshot(self):
        return {str(p.relative_to(self.root)): H.A._identity(p.lstat()) for p in (self.root, *self.root.rglob('*'))}

    def test_three_file_consistency_sha256_readonly_flags_fd_lifecycle(self):
        import resource
        before, initial, limits = self.snapshot(), set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack, ExitStack() as denied:
            for name in ('write', 'pwrite', 'chmod', 'fchmod', 'chown', 'mkdir', 'symlink', 'unlink', 'rename', 'fsync'):
                denied.enter_context(mock.patch.object(H.os, name, side_effect=AssertionError('read only')))
            opened = denied.enter_context(mock.patch.object(H.os, 'open', wraps=H.os.open))
            held = H.hold_host_package_files(stack)
            held['check']()
            self.assertTrue(held['policy']['hostPackageFileMetadataMatched'])
            self.assertEqual(held['policy']['hostPackageFilesObserved'], 3)
            for row in held['policy']['hostPackageFileDigests']:
                self.assertEqual(row['sha256'], hashlib.sha256(self.data[row['path']]).hexdigest())
            for flag in ('hostPackagePublisherAuthenticityVerified', 'hostPackageVersionCompatibilityVerified', 'pamModuleCodeVerified',
                         'effectivePamPolicyVerified', 'effectiveSystemdNamespaceVerified', 'installationAuthorized', 'recoveryAuthorized',
                         'filesWritten', 'servicesActivated', 'installable', 'readyForActivation'):
                self.assertIs(held['policy'][flag], False)
            for call in opened.call_args_list:
                self.assertTrue(call.args[1] & os.O_NOFOLLOW)
                self.assertFalse(call.args[1] & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_checksum_mismatch_refuses_before_any_original_admission(self):
        self.file.write_bytes(b'changed')
        with ExitStack() as stack, mock.patch.object(H.N, 'hold_final_support_namespace') as source:
            with self.assertRaises(ValueError):
                H.hold_final_host_package_files(stack, 'u', '/s', '/r', b'{}')
            source.assert_not_called()

    def test_file_owner_mode_hardlink_symlink_and_bounds_refuse(self):
        self.file.chmod(0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        self.file.chmod(0o644)
        os.chown(self.file, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        os.chown(self.file, 0, 0)
        os.link(self.file, self.root / 'foreign-hardlink')
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        (self.root / 'foreign-hardlink').unlink()
        with ExitStack() as stack, mock.patch.object(H, 'MAX_FILE', 1), self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        self.file.unlink()
        self.file.symlink_to('/etc/passwd')
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)

    def test_nonprotected_ancestors_and_symlinked_parent_refuse(self):
        path = self.file.parent
        path.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        path.chmod(0o755)
        path.rename(path.with_name('saved'))
        path.symlink_to('saved', target_is_directory=True)
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)

    def test_merged_usr_mapping_other_target_owner_and_regular_substitution_refuse(self):
        for target in ('/usr/lib', '../usr/lib', 'usr/lib/../lib', 'absent'):
            self.alias.unlink()
            self.alias.symlink_to(target)
            with ExitStack() as stack, self.assertRaises(ValueError):
                H.hold_host_package_files(stack)
        self.alias.unlink()
        self.alias.symlink_to('usr/lib')
        os.lchown(self.alias, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        self.alias.unlink()
        self.alias.write_bytes(b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)

    def test_later_same_byte_file_inode_replacement_refuses(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            self.file.rename(self.file.with_name('old'))
            self.file.write_bytes(self.data[str(self.file)])
            self.file.chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_alias_inode_or_target_change_refuses(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            self.alias.unlink()
            self.alias.symlink_to('usr/lib')
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_package_version_ownership_or_checksum_table_change_refuses(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            before = self.query.status
            self.query.status = before.replace(b'1.2-test', b'1.3-test')
            with self.assertRaises(ValueError):
                held['check']()
            self.query.status = before
            owners = self.query.owners
            self.query.owners = b'foreign generated'
            with self.assertRaises(ValueError):
                held['check']()
            self.query.owners = owners
            self.query.sums['waydroid'] += b'0' * 32 + b'  usr/share/unrelated\n'
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_bytes_owner_and_mode_change_refuse(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            self.file.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                held['check']()

    def test_metadata_change_during_file_read_is_caught_afterward(self):
        read = H.A._read
        def changed(*args):
            data = read(*args)
            self.query.status = self.query.status.replace(b'1.2-test', b'1.3-test')
            return data
        with ExitStack() as stack, mock.patch.object(H.A, '_read', side_effect=changed), self.assertRaises(ValueError):
            H.hold_host_package_files(stack)

    def test_failure_descriptor_closure_and_budget_before_query(self):
        initial = set(os.listdir('/proc/self/fd'))
        self.file.write_bytes(b'changed')
        with ExitStack() as stack, self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        import resource
        self.queries.reset_mock()
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(1, 1)), self.assertRaises(ValueError):
            H.hold_host_package_files(stack)
        self.queries.assert_not_called()

    def test_combined_one_original_chain_read_guards_and_package_drift(self):
        original = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=b'generated original plan'),
                    'read_file': mock.Mock(return_value=b'generated source'), 'policy': {}}
        with ExitStack() as stack, mock.patch.object(H.N, 'hold_final_support_namespace', return_value=original) as source:
            held = H.hold_final_host_package_files(stack, 'u', '/s', '/r', b'{}')
            source.assert_called_once_with(stack, 'u', '/s', '/r', b'{}')
            self.assertEqual(held['read_plan'](), b'generated original plan')
            self.assertEqual(held['read_file']('/planned'), b'generated source')
            original['check'].side_effect = ValueError('generated original drift')
            with self.assertRaisesRegex(ValueError, 'original drift'):
                held['read_plan']()

    def test_combined_mid_source_read_package_drift_refuses(self):
        def changed():
            self.query.status = self.query.status.replace(b'1.2-test', b'1.3-test')
            return b'generated original plan'
        original = {'check': mock.Mock(), 'read_plan': mock.Mock(side_effect=changed),
                    'read_file': mock.Mock(return_value=b'generated'), 'policy': {}}
        with ExitStack() as stack, mock.patch.object(H.N, 'hold_final_support_namespace', return_value=original):
            held = H.hold_final_host_package_files(stack, 'u', '/s', '/r', b'{}')
            with self.assertRaises(ValueError):
                held['read_plan']()

    def test_later_file_mode_owner_and_ancestor_identity_refuse(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            self.file.chmod(0o600)
            with self.assertRaises(ValueError):
                held['check']()
        self.file.chmod(0o644)
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            os.chown(self.file, 1000, 1000)
            with self.assertRaises(ValueError):
                held['check']()
        os.chown(self.file, 0, 0)
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            path = self.file.parent
            path.rename(path.with_name('old'))
            path.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()

    def test_same_target_alias_replacement_during_readlink_refuses(self):
        with ExitStack() as stack:
            held = H.hold_host_package_files(stack)
            readlink = H.os.readlink
            def changed(*args, **kwargs):
                target = readlink(*args, **kwargs)
                self.alias.unlink()
                self.alias.symlink_to(target)
                return target
            with mock.patch.object(H.os, 'readlink', side_effect=changed), self.assertRaises(ValueError):
                held['check']()

    def test_package_query_failure_refuses_without_original_source_or_files(self):
        with ExitStack() as stack, mock.patch.object(H, '_query', side_effect=subprocess.CalledProcessError(1, 'fixed')):
            with mock.patch.object(H.os, 'open', side_effect=AssertionError('no file open')):
                with mock.patch.object(H.N, 'hold_final_support_namespace') as original:
                    with self.assertRaises(subprocess.SubprocessError):
                        H.hold_final_host_package_files(stack, 'u', '/s', '/r', b'{}')
                    original.assert_not_called()


if __name__ == '__main__':
    unittest.main()
