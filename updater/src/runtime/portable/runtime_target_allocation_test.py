# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-target-allocation.py')
spec = importlib.util.spec_from_file_location('target_allocation', FILE)
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)


class Boundary(unittest.TestCase):
    def test_fixed_paths_and_isolated_fixture_seam(self):
        self.assertEqual(X._parents(('/opt', '/etc')), X.PARENTS)
        self.assertEqual(tuple(map(str, X._parents(('/root/droiddeck-target-allocation-x/opt',
            '/root/droiddeck-target-allocation-x/etc')))),
            ('/root/droiddeck-target-allocation-x/opt', '/root/droiddeck-target-allocation-x/etc'))
        for invalid in (['/opt', '/etc'], ('/opt/', '/etc'), ('/tmp/opt', '/tmp/etc'),
                        ('/opt', '/run'), ('/opt', '/etc/..'), ('/root/droiddeck-target-allocation-x/opt', '/etc')):
            with self.assertRaises(ValueError):
                X._parents(invalid)

    def test_public_api_has_no_path_override_and_no_cli(self):
        with mock.patch.object(X, '_allocate_targets', return_value='generated') as allocate:
            self.assertEqual(X.allocate_targets('stack', 'a' * 64, 'checker'), 'generated')
            self.assertEqual(allocate.call_args.args, ('stack', ('/opt', '/etc'), 'a' * 64, 'checker'))
        for args in ([], ['--install'], ['--allocate'], ['--recover']):
            with self.assertRaises(ValueError):
                X.main(args)
        result = subprocess.run([sys.executable, '-I', '-B', str(FILE), '--allocate'],
            capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No target allocation CLI; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated new-target fixtures required')
class Allocation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-target-allocation-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.parents = (self.base / 'opt', self.base / 'etc')
        for parent in self.parents:
            parent.mkdir(mode=0o755)
        self.source_check = mock.Mock()

    def allocate(self, stack):
        return X._allocate_targets(stack, tuple(map(str, self.parents)), 'a' * 64, self.source_check)

    def empty(self):
        self.assertTrue(all(not list(parent.iterdir()) for parent in self.parents))

    def test_two_exclusive_root700_directories_original_inodes_and_fd_closure(self):
        import resource
        initial = set(os.listdir('/proc/self/fd'))
        limits = resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.allocate(stack)
            report = held['observe']()
            self.assertTrue(report['targetDirectoryOwnershipVerified'])
            self.assertEqual(report['newTargetDirectoriesCreated'], 2)
            for key in ('payloadVerified', 'filesWritten', 'finalPermissionsApplied', 'servicesRegistered',
                        'servicesActivated', 'installationAuthorized', 'recoveryAuthorized',
                        'restartRecoveryVerified', 'persistentStorageVerified', 'installable', 'readyForActivation'):
                self.assertFalse(report[key])
            for parent, role in zip(self.parents, X.ROLES):
                target = parent / X.NAME
                info = target.lstat()
                self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), (0, 0, 0o700))
                self.assertEqual(info.st_ino, os.fstat(held['directory_fds'][role]).st_ino)
                self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)
        self.assertGreater(self.source_check.call_count, 2)
        with self.assertRaises(X.AllocationUncertain):
            held['observe']()

    def test_invalid_digest_or_checker_refuses_without_allocating(self):
        for digest, checker in ((None, lambda: None), ('A' * 64, lambda: None), ('a' * 64, None)):
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'layout digest'):
                X._allocate_targets(stack, tuple(map(str, self.parents)), digest, checker)
            self.empty()

    def test_source_drift_after_first_creation_preserves_only_own_directory(self):
        def changed():
            if (self.parents[0] / X.NAME).exists():
                raise ValueError('generated source drift after creation')
        self.source_check.side_effect = changed
        with ExitStack() as stack, self.assertRaises(X.AllocationUncertain):
            self.allocate(stack)
        self.assertTrue((self.parents[0] / X.NAME).is_dir())
        self.assertEqual(list(self.parents[1].iterdir()), [])

    def test_existing_file_directory_or_dangling_link_never_adopted(self):
        foreign = self.parents[1] / X.NAME
        for kind in ('file', 'directory', 'link'):
            if kind == 'file': foreign.write_bytes(b'generated foreign')
            elif kind == 'directory': foreign.mkdir()
            else: foreign.symlink_to('missing')
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'never be adopted'):
                self.allocate(stack)
            self.assertEqual(list(self.parents[0].iterdir()), [])
            if kind == 'directory': foreign.rmdir()
            else: foreign.unlink()

    def test_source_failure_and_low_descriptor_limit_precede_any_mutation(self):
        import resource
        self.source_check.side_effect = ValueError('generated source drift')
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'source drift'):
            self.allocate(stack)
        self.empty()
        self.source_check.side_effect = None
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(4, 4)), self.assertRaisesRegex(ValueError, 'descriptor'):
            self.allocate(stack)
        self.empty()

    def test_writable_or_linked_parent_refuses_before_creation(self):
        self.parents[1].chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError): self.allocate(stack)
        self.empty()
        self.parents[1].chmod(0o755)
        self.parents[1].rmdir()
        self.parents[1].symlink_to(self.parents[0], target_is_directory=True)
        with ExitStack() as stack, self.assertRaises(ValueError): self.allocate(stack)
        self.assertEqual(list(self.parents[0].iterdir()), [])

    def test_second_mkdir_race_preserves_first_and_foreign_config(self):
        real, calls = os.mkdir, 0
        def raced(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.parents[1] / X.NAME).symlink_to('generated-foreign')
            return real(*args, **kwargs)
        with ExitStack() as stack, mock.patch.object(os, 'mkdir', side_effect=raced), self.assertRaises(X.AllocationUncertain):
            self.allocate(stack)
        self.assertTrue((self.parents[0] / X.NAME).is_dir())
        self.assertEqual(os.readlink(self.parents[1] / X.NAME), 'generated-foreign')

    def test_fsync_failure_preserves_new_target_and_no_second_creation(self):
        with ExitStack() as stack, mock.patch.object(os, 'fsync', side_effect=OSError('generated disk failure')), self.assertRaises(X.AllocationUncertain):
            self.allocate(stack)
        self.assertTrue((self.parents[0] / X.NAME).is_dir())
        self.assertEqual(list(self.parents[1].iterdir()), [])

    def test_target_replacement_poisoned_and_never_rebound(self):
        with ExitStack() as stack:
            held = self.allocate(stack)
            target = self.parents[0] / X.NAME
            target.rename(self.parents[0] / 'preserved-original')
            target.mkdir(mode=0o700)
            with self.assertRaises(X.AllocationUncertain): held['observe']()
            with self.assertRaises(X.AllocationUncertain): held['observe']()

    def test_parent_replacement_and_target_mode_drift_refused(self):
        with ExitStack() as stack:
            held = self.allocate(stack)
            (self.parents[0] / X.NAME).chmod(0o755)
            with self.assertRaises(X.AllocationUncertain): held['observe']()
        # Do not retry/adopt allocated trees; independent fixture for parent replacement.
        with tempfile.TemporaryDirectory(prefix='droiddeck-target-allocation-', dir='/root') as base:
            parents = tuple(Path(base) / name for name in ('opt', 'etc'))
            for parent in parents: parent.mkdir()
            with ExitStack() as stack:
                held = X._allocate_targets(stack, tuple(map(str, parents)), 'b' * 64, lambda: None)
                parents[1].rename(Path(base) / 'preserved-parent')
                parents[1].mkdir()
                with self.assertRaises(X.AllocationUncertain): held['check']()

    def test_process_exit_closes_ownership_but_never_authorizes_retry(self):
        program = ('import importlib.util, sys\nfrom contextlib import ExitStack\n'
            's=importlib.util.spec_from_file_location("generated", sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n'
            'with ExitStack() as stack: m._allocate_targets(stack, tuple(sys.argv[2:]), "a"*64, lambda: None)\n')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(FILE), *map(str, self.parents)],
            capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        identities = [(parent / X.NAME).stat().st_ino for parent in self.parents]
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'never be adopted'):
            self.allocate(stack)
        self.assertEqual([(parent / X.NAME).stat().st_ino for parent in self.parents], identities)


if __name__ == '__main__':
    unittest.main()
