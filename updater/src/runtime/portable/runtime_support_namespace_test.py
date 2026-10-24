# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-support-namespace.py')
spec = importlib.util.spec_from_file_location('support_namespace_tests', FILE)
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)


class Boundary(unittest.TestCase):
    def test_shared_support_recipe_validator_is_exact_and_bounded(self):
        raw = b'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root 1201 -\n'
        for gid in (b'1', b'1201', b'2147483647'):
            self.assertIsNone(N._validate_support_recipes(N.PAM, raw.replace(b'1201', gid)))
        for gid in (b'0', b'01', b'-1', b'2147483648', b'99999999999', b'root'):
            with self.subTest(gid=gid), self.assertRaises(ValueError):
                N._validate_support_recipes(N.PAM, raw.replace(b'1201', gid))
        for pam, tmpfiles in ((bytearray(N.PAM), raw), (N.PAM, bytearray(raw)),
                             (None, raw), (N.PAM, None), (N.PAM + b'\n', raw),
                             (N.PAM, raw + b'\n'), (N.PAM, raw.replace(b'0750', b'0777')),
                             (N.PAM, raw.replace(b'/run/droiddeck', b'/run/foreign'))):
            with self.assertRaises(ValueError): N._validate_support_recipes(pam, tmpfiles)

    def test_explicit_runtime_gid_binding_refuses_other_or_invalid_groups(self):
        raw = b'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root 1201 -\n'
        N._validate_support_recipes(N.PAM, raw, expected_gid=1201)
        for gid in (True, '1201', 0, 999, 1202, 2**31):
            with self.subTest(gid=gid), self.assertRaisesRegex(ValueError, 'runtime account group'):
                N._validate_support_recipes(N.PAM, raw, expected_gid=gid)

    def test_fixed_names_paths_and_closed_redacted_cli(self):
        self.assertEqual(tuple(map(str, N.PAM_DIRS)), ('/etc/pam.d', '/usr/lib/pam.d'))
        self.assertEqual(tuple(map(str, N.TMP_DIRS)), ('/etc/tmpfiles.d', '/run/tmpfiles.d', '/usr/local/lib/tmpfiles.d', '/usr/lib/tmpfiles.d'))
        self.assertEqual(tuple(map(str, N.STATE_ROOTS)), ('/var/lib/droiddeck', '/run/droiddeck'))
        for args in ([], ['--install'], ['--recover'], ['--inspect-support-namespace', '/foreign'],
                     ['--inspect-final-support-namespace', 'u', '/s', '/r', '-', '--vendor', '/foreign']):
            with self.assertRaises(ValueError):
                N.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Support namespace preflight refused; no files or services changed')

    def test_privilege_bounds_and_descriptor_refusal_before_opens(self):
        with ExitStack() as stack, mock.patch.object(N.os, 'open', side_effect=AssertionError('no open')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(N.A.sys, 'platform', platform), mock.patch.object(N.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        N.hold_support_namespace(stack)
            with mock.patch.object(N.A, '_root_linux'):
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        N.hold_final_support_namespace(stack, 'u', '/s', '/r', raw)
                fake = SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(1, 1)))
                with mock.patch.dict(sys.modules, resource=fake), mock.patch.object(N.os, 'listdir', return_value=['0', '1', '2']):
                    with self.assertRaises(ValueError):
                        N.hold_support_namespace(stack)

    def test_io_errors_are_not_absence(self):
        for error in (PermissionError(), NotADirectoryError(), OSError('generated')):
            with mock.patch.object(N.R.os, 'stat', side_effect=error), self.assertRaises(OSError):
                N.R._absent(7, 'name')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated support namespace fixtures required')
class Namespace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-support-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pam = tuple(PurePosixPath(str(self.root) + str(p)) for p in N.PAM_DIRS)
        self.tmp = tuple(PurePosixPath(str(self.root) + str(p)) for p in N.TMP_DIRS)
        self.states = tuple(PurePosixPath(str(self.root) + str(p)) for p in N.STATE_ROOTS)
        for path in (*self.pam, *self.tmp, *(p.parent for p in self.states)):
            Path(path).mkdir(parents=True, exist_ok=True, mode=0o755)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(N, 'PAM_DIRS', self.pam).start()
        mock.patch.object(N, 'TMP_DIRS', self.tmp).start()
        mock.patch.object(N, 'STATE_ROOTS', self.states).start()

    def snapshot(self):
        return {str(p.relative_to(self.root)): N.A._identity(p.lstat()) for p in (self.root, *self.root.rglob('*'))}

    def prepare_state(self):
        path = Path(self.states[0])
        path.mkdir(mode=0o700)
        for name in ('image-staging', 'waydroid-profile'):
            (path / name).mkdir(mode=0o700)
        return path

    def test_prepared_state_is_readonly_and_does_not_adopt_runtime_state(self):
        path = self.prepare_state()
        before, descriptors = self.snapshot(), set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack, prepared_state=True)
            held['check']()
            self.assertEqual(held['policy']['runtimeStateRootsAbsentObserved'], 1)
            self.assertTrue(held['policy']['preparationStateNamespaceVerified'])
            self.assertFalse(held['policy']['runtimeStateCreated'])
            self.assertEqual(self.snapshot(), before)
            (path / 'browser-connection').mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                held['check']()
            (path / 'browser-connection').rmdir()
            Path(self.states[1]).mkdir()
            with self.assertRaises(ValueError):
                held['check']()
            Path(self.states[1]).rmdir()
        self.assertEqual(set(os.listdir('/proc/self/fd')), descriptors)

    def test_prepared_state_requires_closed_private_nofollow_directories(self):
        path = self.prepare_state()
        for target in (path, path / 'image-staging', path / 'waydroid-profile'):
            target.chmod(0o755)
            with ExitStack() as stack, self.assertRaises(ValueError):
                N.hold_support_namespace(stack, prepared_state=True)
            target.chmod(0o700)
        child = path / 'waydroid-profile'
        child.rmdir()
        for kind in ('absent', 'symlink', 'file'):
            if kind == 'symlink': child.symlink_to('image-staging')
            if kind == 'file': child.write_bytes(b'generated')
            with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
                N.hold_support_namespace(stack, prepared_state=True)
            if kind != 'absent': child.unlink()

    def test_prepared_directory_replacement_and_mode_drift_refuse(self):
        path = self.prepare_state()
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack, prepared_state=True)
            child = path / 'image-staging'
            child.chmod(0o755)
            with self.assertRaises(ValueError): held['check']()
            child.chmod(0o700)
            child.rename(path / 'saved')
            child.mkdir(mode=0o700)
            (path / 'saved').rmdir()
            with self.assertRaises(ValueError): held['check']()

    def final(self, stack):
        return N.hold_final_support_namespace(stack, 'u', '/root/source', '/root/recipes', b'{}')

    def source(self):
        files = {'/etc/pam.d/droiddeck': N.PAM,
            '/etc/tmpfiles.d/droiddeck.conf': b'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root 1201 -\n'}
        return {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=b'original-layout'),
                'read_file': mock.Mock(side_effect=files.__getitem__), 'policy': {'originalContext': 'generated'}}, files

    def test_absence_complete_inventory_readonly_nofollow_fd_lifecycle(self):
        import resource
        for path in (*self.pam, *self.tmp):
            (Path(path) / 'unrelated').symlink_to('/absent-generated')
        before, initial, limits = self.snapshot(), set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack, ExitStack() as denied:
            for name in ('write', 'chmod', 'fchmod', 'chown', 'mkdir', 'symlink', 'unlink', 'rename', 'fsync', 'pread', 'readlink'):
                denied.enter_context(mock.patch.object(N.os, name, side_effect=AssertionError('metadata only')))
            denied.enter_context(mock.patch.object(N.U.subprocess, 'run', side_effect=AssertionError('no commands')))
            opened = denied.enter_context(mock.patch.object(N.os, 'open', wraps=N.os.open))
            held = N.hold_support_namespace(stack)
            held['check']()
            self.assertIs(held['policy']['supportNamePreflightVerified'], True)
            self.assertEqual(held['policy']['supportEntriesObserved'], 6)
            for flag in ('effectivePamPolicyVerified', 'pamVendorBuildPathsVerified', 'pamModuleCodeVerified', 'effectiveTmpfilesPolicyVerified',
                         'tmpfilesRuleConflictsVerified', 'filesWritten', 'runtimeStateCreated', 'targetReserved', 'targetOwnershipClaimed',
                         'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation'):
                self.assertIs(held['policy'][flag], False)
            for call in opened.call_args_list:
                self.assertTrue(call.args[1] & os.O_NOFOLLOW)
                self.assertTrue(call.args[1] & os.O_DIRECTORY)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_each_owned_pam_tmpfiles_state_kind_refuses_before_source_admission(self):
        targets = (*(p / 'droiddeck' for p in self.pam), *(p / 'droiddeck.conf' for p in self.tmp), *self.states)
        for path in map(Path, targets):
            for kind in ('file', 'directory', 'symlink', 'fifo'):
                if kind == 'file':
                    path.write_bytes(b'generated')
                elif kind == 'directory':
                    path.mkdir()
                elif kind == 'symlink':
                    path.symlink_to('absent-generated')
                else:
                    os.mkfifo(path)
                before = self.snapshot()
                with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace') as source:
                    with self.assertRaises(ValueError):
                        self.final(stack)
                    source.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                path.rmdir() if kind == 'directory' else path.unlink()

    def test_missing_optional_parents_not_created_and_appearance_invalidates(self):
        shutil.rmtree(self.root / 'usr')
        before = self.snapshot()
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            self.assertEqual(self.snapshot(), before)
            (self.root / 'usr').mkdir()
            with self.assertRaises(ValueError):
                held['check']()

    def test_missing_etc_pam_directory_refuses_without_reading_legacy_config(self):
        Path(self.pam[0]).rmdir()
        (self.root / 'etc/pam.conf').write_bytes(b'generated legacy config, never read')
        with ExitStack() as stack, mock.patch.object(N.os, 'pread', side_effect=AssertionError('no legacy content')):
            with self.assertRaisesRegex(ValueError, 'legacy PAM fallback'):
                N.hold_support_namespace(stack)

    def test_symlinked_config_roots_and_state_ancestors_refuse(self):
        for path in map(Path, (*self.pam, *self.tmp, self.states[0].parent)):
            saved = path.with_name(path.name + '-saved')
            path.rename(saved)
            path.symlink_to(saved.name, target_is_directory=True)
            with ExitStack() as stack, self.assertRaises(ValueError):
                N.hold_support_namespace(stack)
            path.unlink()
            saved.rename(path)

    def test_unprotected_directory_mode_or_owner_refuses(self):
        path = Path(self.tmp[0])
        path.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            N.hold_support_namespace(stack)
        path.chmod(0o755)
        os.chown(path, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            N.hold_support_namespace(stack)

    def test_later_state_appearance_refuses_without_traversing_it(self):
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            Path(self.states[0]).mkdir(mode=0o700)
            (Path(self.states[0]) / 'do-not-read').write_bytes(b'generated')
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_owned_config_mask_and_inventory_change_refuse(self):
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            path = Path(self.tmp[2]) / 'droiddeck.conf'
            path.symlink_to('/dev/null')
            with self.assertRaises(ValueError):
                held['check']()
        path.unlink()
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            (Path(self.pam[1]) / 'unrelated').write_bytes(b'generated')
            with self.assertRaises(ValueError):
                held['check']()

    def test_same_name_same_byte_inventory_inode_replacement_refuses(self):
        path = Path(self.tmp[0]) / 'unrelated.conf'
        path.write_bytes(b'generated')
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            path.rename(path.with_name('old'))
            path.write_bytes(b'generated')
            path.with_name('old').unlink()
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_ancestor_replacement_or_metadata_change_refuses(self):
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            path = Path(self.tmp[0])
            path.rename(path.with_name('saved'))
            path.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()

    def test_inventory_limit_refuses_and_failure_closes_descriptors(self):
        for i in range(3):
            (Path(self.pam[0]) / str(i)).touch()
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack, mock.patch.object(N, 'MAX_ENTRIES', 2), self.assertRaises(ValueError):
            N.hold_support_namespace(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_scan_race_and_io_failures_are_not_absence(self):
        original = N.os.scandir
        changed = False
        def raced(fd):
            nonlocal changed
            if not changed:
                changed = True
                (Path(self.pam[0]) / 'droiddeck').touch()
            return original(fd)
        with ExitStack() as stack, mock.patch.object(N.os, 'scandir', side_effect=raced), self.assertRaises(ValueError):
            N.hold_support_namespace(stack)

    def test_combined_composes_single_original_chain_and_guarded_recipe_reads(self):
        held, files = self.source()
        with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', return_value=held) as source:
            result = self.final(stack)
            source.assert_called_once_with(stack, 'u', '/root/source', '/root/recipes', b'{}')
            self.assertEqual(held['read_file'].call_count, 2)
            self.assertEqual(result['read_plan'](), b'original-layout')
            self.assertEqual(result['read_file']('/etc/pam.d/droiddeck'), files['/etc/pam.d/droiddeck'])
            self.assertIs(result['policy']['supportRecipePathsBound'], True)
            self.assertIs(result['policy']['effectiveTmpfilesPolicyVerified'], False)

    def test_combined_recipe_paths_modes_and_gid_must_match_reviewed_scope(self):
        held, files = self.source()
        original = files['/etc/tmpfiles.d/droiddeck.conf']
        for changed in (b'd /foreign 0700 root root -\n', original.replace(b'1201', b'0'),
                        original.replace(b'1201', b'2147483648'), original.replace(b'0750', b'0777')):
            files['/etc/tmpfiles.d/droiddeck.conf'] = changed
            with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', return_value=held), self.assertRaises(ValueError):
                self.final(stack)
        held, files = self.source()
        files['/etc/pam.d/droiddeck'] = b'foreign generated'
        with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', return_value=held), self.assertRaises(ValueError):
            self.final(stack)

    def test_combined_midread_namespace_or_original_drift_refuses(self):
        held, files = self.source()
        def changed(name):
            Path(self.states[1]).mkdir(exist_ok=True)
            return files[name]
        held['read_file'].side_effect = changed
        with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', return_value=held), self.assertRaises(ValueError):
            self.final(stack)
        Path(self.states[1]).rmdir()
        held, files = self.source()
        with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', return_value=held):
            result = self.final(stack)
            held['check'].side_effect = ValueError('generated original drift')
            with self.assertRaisesRegex(ValueError, 'original drift'):
                result['read_plan']()

    def test_later_held_directory_mode_or_owner_change_refuses(self):
        path = Path(self.pam[1])
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            path.chmod(0o777)
            with self.assertRaises(ValueError):
                held['check']()
        path.chmod(0o755)
        with ExitStack() as stack:
            held = N.hold_support_namespace(stack)
            os.chown(path, 1000, 1000)
            with self.assertRaises(ValueError):
                held['check']()


if __name__ == '__main__':
    unittest.main()
