# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-unit-namespace.py')
spec = importlib.util.spec_from_file_location('unit_namespace_tests', FILE)
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)


def manager_rows():
    return '\n\n'.join('Id=' + unit + '\nLoadState=not-found\nActiveState=inactive\nFragmentPath=\nDropInPaths='
                       for unit in U.OWNED) + '\n'


class Boundary(unittest.TestCase):
    def test_reviewed_paths_four_owned_units_and_twelve_dropin_names(self):
        self.assertEqual(len(U.LOAD_PATHS), 12)
        self.assertEqual(len(U.OWNED), 4)
        self.assertNotIn('waydroid-container.service', U.OWNED)
        self.assertEqual(len(U.dropin_names()), 12)
        for name in ('service.d', 'tesla-.service.d', 'tesla-waydroid-.service.d',
                     'tesla-waydroid-headless-.service.d', 'droiddeck-.service.d',
                     'droiddeck-browser-.service.d', 'waydroid-.service.d'):
            self.assertIn(name, U.dropin_names())

    def test_manager_exact_paths_closed_units_no_loaded_or_active_adoption(self):
        paths = ' '.join(map(str, U.LOAD_PATHS)) + '\n'
        with mock.patch.object(U, '_query', side_effect=[paths, manager_rows()]):
            U._manager()
        for raw in (manager_rows().replace('not-found', 'loaded', 1),
                    manager_rows().replace('inactive', 'active', 1),
                    manager_rows().replace('FragmentPath=', 'FragmentPath=/private', 1),
                    manager_rows().replace('DropInPaths=', 'DropInPaths=/private', 1),
                    manager_rows().replace('LoadState=not-found\n', '', 1), manager_rows() + '\n' + manager_rows()):
            with mock.patch.object(U, '_query', side_effect=[paths, raw]), self.assertRaises(ValueError):
                U._manager()
        with mock.patch.object(U, '_query', return_value=paths + '/foreign'), self.assertRaises(ValueError):
            U._manager()

    def test_fixed_query_environment_bound_timeout_and_redacted_cli(self):
        result = mock.Mock(stdout=b'generated\n')
        with mock.patch.object(U.subprocess, 'run', return_value=result) as run:
            self.assertEqual(U._query('show'), 'generated\n')
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/usr/bin/systemctl', 'show'])
            self.assertEqual((kwargs['cwd'], kwargs['timeout'], kwargs['check']), ('/', 5, True))
            self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'LC_ALL', 'SYSTEMD_PAGER'})
            for raw in (b'', b'x' * 16385):
                result.stdout = raw
                with self.assertRaises(ValueError):
                    U._query('show')
        for args in ([], ['--install'], ['--recover'], ['--inspect-unit-namespace', '/foreign']):
            with self.assertRaises(ValueError):
                U.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Systemd namespace preflight refused; no files or services changed')

    def test_privilege_and_bounded_settings_before_manager_or_filesystem(self):
        with ExitStack() as stack, mock.patch.object(U, '_manager', side_effect=AssertionError('no query')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(U.A.sys, 'platform', platform), mock.patch.object(U.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        U.hold_unit_namespace(stack)
            with mock.patch.object(U.A, '_root_linux'):
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        U.hold_final_unit_namespace(stack, 'u', '/s', '/r', raw)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated unit namespace fixtures required')
class Namespace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-units-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = tuple(PurePosixPath(str(self.root / str(i))) for i in range(12))
        for path in self.paths:
            Path(path).mkdir(mode=0o700)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(U, 'LOAD_PATHS', self.paths).start()
        self.query = mock.patch.object(U, '_query', side_effect=lambda *args:
            ' '.join(map(str, self.paths)) + '\n' if '--property=UnitPath' in args else manager_rows()).start()

    def test_read_only_positive_all_paths_and_fd_limit_lifecycle(self):
        import resource
        before = set(self.root.rglob('*'))
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            held['check']()
            self.assertEqual(held['policy']['unitSearchPathsChecked'], 12)
            self.assertEqual(held['policy']['dropInNamesCheckedPerPath'], 12)
            self.assertIs(held['policy']['ownedSystemdNamespacePreflightVerified'], True)
            for flag in ('effectiveSystemdNamespaceVerified', 'registrationNamespaceVerified', 'filesWritten',
                         'targetReserved', 'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized'):
                self.assertIs(held['policy'][flag], False)
        self.assertEqual(set(self.root.rglob('*')), before)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)

    def test_all_owned_units_in_each_path_file_mask_directory_fifo_refuse(self):
        for parent in self.paths:
            for unit in U.OWNED:
                path = Path(parent / unit)
                for kind in ('file', 'mask', 'directory', 'fifo'):
                    if kind == 'file':
                        path.write_bytes(b'generated')
                    elif kind == 'mask':
                        path.symlink_to('/dev/null')
                    elif kind == 'directory':
                        path.mkdir()
                    else:
                        os.mkfifo(path)
                    with ExitStack() as stack, self.assertRaises(ValueError):
                        U.hold_unit_namespace(stack)
                    path.rmdir() if kind == 'directory' else path.unlink()

    def test_alias_chain_across_paths_and_dangling_direct_alias_refuse(self):
        first, second = Path(self.paths[0]) / 'first.service', Path(self.paths[1]) / 'second.service'
        first.symlink_to('second.service')
        second.symlink_to('/absent/' + U.OWNED[0])
        with ExitStack() as stack, self.assertRaises(ValueError):
            U.hold_unit_namespace(stack)

    def test_all_exact_prefix_global_dropins_in_each_path_refuse(self):
        for parent in self.paths:
            for name in U.dropin_names():
                directory = Path(parent / name)
                directory.mkdir()
                path = directory / 'foreign.conf'
                path.symlink_to('absent')
                with ExitStack() as stack, self.assertRaises(ValueError):
                    U.hold_unit_namespace(stack)
                path.unlink()
                directory.rmdir()

    def test_enablement_and_dependency_links_names_and_targets_refuse(self):
        for suffix in U.SUFFIXES:
            directory = Path(self.paths[0] / ('generated.target' + suffix))
            directory.mkdir()
            for name, target in ((U.OWNED[0], 'absent'), ('alias.service', '/elsewhere/' + U.OWNED[0])):
                path = directory / name
                path.symlink_to(target)
                with ExitStack() as stack, self.assertRaises(ValueError):
                    U.hold_unit_namespace(stack)
                path.unlink()
            directory.rmdir()
        directory = Path(self.paths[0] / (U.OWNED[0] + '.wants'))
        directory.mkdir()
        (directory / 'foreign.service').write_bytes(b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            U.hold_unit_namespace(stack)

    def test_unrelated_dependency_inventory_is_retained_not_executed(self):
        directory = Path(self.paths[0] / 'generated.target.wants')
        directory.mkdir()
        path = directory / 'unrelated.service'
        path.symlink_to('/absent/unrelated.service')
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            path.unlink()
            path.symlink_to('/different/unrelated.service')
            with self.assertRaises(ValueError):
                held['check']()

    def test_missing_root_parent_later_appearance_refuses_without_creation(self):
        path = Path(self.paths[0])
        path.rmdir()
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            self.assertFalse(path.exists())
            path.mkdir()
            with self.assertRaises(ValueError):
                held['check']()

    def test_parent_symlink_owner_modes_and_replacement_refuse(self):
        path = Path(self.paths[0])
        path.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            U.hold_unit_namespace(stack)
        path.chmod(0o700)
        os.chown(path, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            U.hold_unit_namespace(stack)
        os.chown(path, 0, 0)
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            path.rename(path.with_name('old'))
            path.mkdir()
            with self.assertRaises(ValueError):
                held['check']()
        path.rmdir()
        path.symlink_to('1')
        with ExitStack() as stack, self.assertRaises(OSError):
            U.hold_unit_namespace(stack)

    def test_symlinked_dependency_directory_refuses_without_traversal(self):
        path = Path(self.paths[0] / 'generated.target.wants')
        path.symlink_to(self.paths[1])
        with ExitStack() as stack, self.assertRaises(OSError):
            U.hold_unit_namespace(stack)

    def test_inventory_mutation_and_manager_generation_drift_invalidate_hold(self):
        path = Path(self.paths[0] / 'unrelated.service')
        path.write_bytes(b'generated')
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                held['check']()
        with ExitStack() as stack:
            held = U.hold_unit_namespace(stack)
            self.query.side_effect = lambda *args: '/foreign\n'
            with self.assertRaises(ValueError):
                held['check']()

    def test_descriptor_and_inventory_budget_failure_closes_fds(self):
        initial = set(os.listdir('/proc/self/fd'))
        for attribute, count in (('MAX_DIRECTORIES', 1), ('MAX_ENTRIES', 0)):
            path = Path(self.paths[0] / 'unrelated.service')
            path.touch(exist_ok=True)
            with ExitStack() as stack, mock.patch.object(U, attribute, count), self.assertRaises(ValueError):
                U.hold_unit_namespace(stack)
            self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_loaded_manager_unit_refuses_before_original_source_admission(self):
        self.query.side_effect = lambda *args: (' '.join(map(str, self.paths)) if '--property=UnitPath' in args
            else manager_rows().replace('not-found', 'loaded', 1))
        with ExitStack() as stack, mock.patch.object(U.R, 'hold_final_registration_paths', side_effect=AssertionError('no source')):
            with self.assertRaises(ValueError):
                U.hold_final_unit_namespace(stack, 'u', '/s', '/r', b'{}')

    def test_one_source_hold_and_mid_read_namespace_drift_refuses(self):
        held = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=b'generated'),
                'read_file': mock.Mock(return_value=b'generated'), 'policy': {'finalLayoutVerified': True}}
        with ExitStack() as stack, mock.patch.object(U.R, 'hold_final_registration_paths', return_value=held) as admit:
            result = U.hold_final_unit_namespace(stack, 'u', '/s', '/r', b'{}')
            admit.assert_called_once_with(stack, 'u', '/s', '/r', b'{}')
            self.assertEqual(result['read_plan'](), b'generated')
            self.assertEqual(result['read_file']('generated'), b'generated')
            def change(_):
                Path(self.paths[0] / 'new.service').write_bytes(b'generated')
                return b'generated'
            held['read_file'].side_effect = change
            with self.assertRaises(ValueError):
                result['read_file']('generated')


if __name__ == '__main__':
    unittest.main()
