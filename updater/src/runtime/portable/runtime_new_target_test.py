# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
FILE = HERE / 'runtime-new-target.py'
spec = importlib.util.spec_from_file_location('new_target_tests', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def test_fixed_target_and_no_cli_override_or_install(self):
        self.assertEqual(P.TARGET_PARENT / P.TARGET_NAME, PurePosixPath('/opt/droiddeck'))
        self.assertEqual(P.L.TARGET, P.TARGET_PARENT / P.TARGET_NAME)
        for args in ([], ['--install'], ['--inspect-new-target', 'hilman', '/root/source', '/private-value']):
            with self.assertRaises(ValueError):
                P.main(args)
        with mock.patch.object(P.A, '_root_linux'):
            with ExitStack() as stack, self.assertRaises(ValueError):
                P._hold_new_target(stack, '/root/fixture', '../outside')

    def test_privilege_and_source_namespace_before_target_or_package_reads(self):
        with ExitStack() as stack, mock.patch.object(P, '_hold_new_target', side_effect=AssertionError('no target reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.A.sys, 'platform', platform), \
                        mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_new_runtime_target(stack, 'fixture', '/root/source')
            with mock.patch.object(P.A, '_root_linux'):
                for source in ('/', '/opt', '/opt/droiddeck', '/opt/droiddeck/child', '/root//source'):
                    with self.assertRaises(ValueError):
                        P.hold_new_runtime_target(stack, 'fixture', source)

    def test_existing_target_refuses_before_source_admission(self):
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P, '_hold_new_target', side_effect=ValueError('populated')), \
                mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=AssertionError('no source reads')):
            with self.assertRaises(ValueError):
                P.hold_new_runtime_target(stack, 'fixture', '/root/source')

    def test_plan_and_payload_reads_recheck_namespace_and_source_before_return(self):
        target = {'check': mock.Mock(), 'parent_fd': 17}
        plan = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=b'fixed plan'),
                'read_source': mock.Mock(return_value=b'fixed payload'), 'policy': {'installable': False}}
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P, '_hold_new_target', return_value=target), \
                mock.patch.object(P.L, 'hold_runtime_install_plan', return_value=plan):
            held = P.hold_new_runtime_target(stack, 'fixture', '/root/source')
            self.assertIs(held['policy']['targetNamespaceVerified'], True)
            for flag in ('targetReserved', 'installationAuthorized', 'installable'):
                self.assertIs(held['policy'][flag], False)
            self.assertEqual(held['parent_fd'], 17)
            target['check'].reset_mock()
            plan['check'].reset_mock()
            self.assertEqual(held['read_plan'](), b'fixed plan')
            self.assertEqual(target['check'].call_count, 4)
            self.assertEqual(plan['check'].call_count, 2)
            def changed(name):
                target['check'].side_effect = ValueError('target appeared')
                return b'payload'
            plan['read_source'].side_effect = changed
            with self.assertRaises(ValueError):
                held['read_source']('artifacts/location-bridge.jar')
            target['check'].side_effect = None
            plan['check'].side_effect = ValueError('source or account changed')
            with self.assertRaises(ValueError):
                held['read_plan']()

    def test_target_drift_during_source_acquisition_prevents_return(self):
        target = {'check': mock.Mock(), 'parent_fd': 17}
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P, '_hold_new_target', return_value=target):
            def changed(*args):
                target['check'].side_effect = ValueError('target appeared')
                return {'check': mock.Mock()}
            with mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=changed), self.assertRaises(ValueError):
                P.hold_new_runtime_target(stack, 'fixture', '/root/source')

    def test_cli_fixed_failure_does_not_leak_arguments(self):
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'New runtime target admission refused; no changes performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only new-target fixtures required')
class Namespace(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='droiddeck-new-target-', dir='/root'))
        self.addCleanup(shutil.rmtree, self.root)
        self.parent = self.root / 'parent'
        self.parent.mkdir(mode=0o755)
        self.target = self.parent / 'droiddeck'

    def hold(self, stack):
        return P._hold_new_target(stack, self.parent, 'droiddeck')

    def test_absent_target_holds_original_parent_and_ignores_unrelated_entries(self):
        before = set(os.listdir('/proc/self/fd'))
        marker = self.parent / 'unrelated'
        marker.write_bytes(b'original marker')
        with ExitStack() as stack:
            held = self.hold(stack)
            self.assertEqual(os.fstat(held['parent_fd']).st_ino, self.parent.stat().st_ino)
            (self.parent / 'other').mkdir(mode=0o700)
            held['check']()
            self.assertEqual(marker.read_bytes(), b'original marker')
            self.assertFalse(self.target.exists())
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_all_existing_target_types_refuse_without_adopting_or_removing(self):
        for kind in ('file', 'directory', 'dangling-symlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    self.target.write_bytes(b'generated existing file')
                elif kind == 'directory':
                    self.target.mkdir(mode=0o700)
                elif kind == 'dangling-symlink':
                    self.target.symlink_to(self.root / 'missing')
                else:
                    os.mkfifo(self.target, 0o600)
                before = self.target.lstat()
                with ExitStack() as stack, self.assertRaises(ValueError):
                    self.hold(stack)
                self.assertEqual(P.A._identity(self.target.lstat()), P.A._identity(before))
                if kind == 'directory':
                    self.target.rmdir()
                else:
                    self.target.unlink()

    def test_newly_appearing_target_invalidates_live_admission(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.target.symlink_to(self.root / 'missing')
            with self.assertRaises(ValueError):
                held['check']()
            self.assertTrue(self.target.is_symlink())

    def test_parent_replacement_permissions_or_owner_drift_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.parent.rename(self.root / 'retained-parent')
            self.parent.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()
        self.parent.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.parent.chmod(0o755)
        os.chown(self.parent, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_linked_ancestor_refuses_and_failed_acquisition_closes_descriptors(self):
        before = set(os.listdir('/proc/self/fd'))
        linked = self.root / 'linked'
        linked.symlink_to(self.parent, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            P._hold_new_target(stack, linked, 'droiddeck')
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_stat_permission_and_io_errors_are_not_treated_as_absence(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            original = os.stat
            for error in (PermissionError('generated permission failure'), OSError(5, 'generated I/O failure')):
                def changed(name, *args, **kwargs):
                    if name == 'droiddeck':
                        raise error
                    return original(name, *args, **kwargs)
                with mock.patch.object(P.os, 'stat', side_effect=changed), self.assertRaises(OSError):
                    held['check']()


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only composed new-target fixtures required')
class Composed(unittest.TestCase):
    def _fixed_target(self, stack, parent, name):
        self.assertEqual((parent, name), (PurePosixPath('/opt'), 'droiddeck'))
        return self.original_target(stack, self.parent, name)

    def setUp(self):
        Namespace.setUp(self)
        self.original_target = P._hold_new_target
        patcher = mock.patch.object(P, '_hold_new_target', side_effect=self._fixed_target)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly'

    def test_composed_real_nss_source_and_plan_under_generated_target_parent(self):
        with ExitStack() as stack:
            held = P.hold_new_runtime_target(stack, 'hilman', self.source)
            self.assertEqual(len(json.loads(held['read_plan']())['files']), 229)
            self.assertTrue(held['read_source']('artifacts/location-bridge.jar'))
            self.assertEqual(held['policy']['assemblyFilesVerified'], 235)
            self.assertEqual(held['policy']['placementLinksVerified'], 4)
            for flag in ('targetNamespaceVerified', 'targetAbsentObserved', 'targetAccountMatched'):
                self.assertIs(held['policy'][flag], True)
            for flag in (*[key for key in P.L.FALSE_FLAGS if key != 'targetNamespaceVerified'],
                         'targetReserved', 'installationAuthorized', 'rootPrivateCopyVerified', 'rootPrivateArtifactCopyVerified'):
                self.assertIs(held['policy'][flag], False)
            self.assertFalse(self.target.exists())
            held['check']()

    def test_target_created_during_plan_read_prevents_return(self):
        original = P.L.hold_runtime_install_plan
        def wrapped(*args):
            plan = original(*args)
            def read():
                raw = plan['read_plan']()
                self.target.mkdir(mode=0o700)
                return raw
            return {**plan, 'read_plan': read}
        with mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=wrapped), ExitStack() as stack:
            held = P.hold_new_runtime_target(stack, 'hilman', self.source)
            with self.assertRaises(ValueError):
                held['read_plan']()
            self.assertTrue(self.target.is_dir())

    def test_target_created_during_payload_read_prevents_return(self):
        original = P.L.hold_runtime_install_plan
        def wrapped(*args):
            plan = original(*args)
            def read(name):
                raw = plan['read_source'](name)
                self.target.symlink_to(self.root / 'missing')
                return raw
            return {**plan, 'read_source': read}
        with mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=wrapped), ExitStack() as stack:
            held = P.hold_new_runtime_target(stack, 'hilman', self.source)
            with self.assertRaises(ValueError):
                held['read_source']('artifacts/location-bridge.jar')
            self.assertTrue(self.target.is_symlink())


if __name__ == '__main__':
    unittest.main()
