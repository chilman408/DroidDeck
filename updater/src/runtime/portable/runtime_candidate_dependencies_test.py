# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import runtime_candidate_admission_test as fixtures

HERE = Path(__file__).parent
FILE = HERE / 'runtime-candidate-dependencies.py'
P = None
if FILE.exists():
    spec = importlib.util.spec_from_file_location('candidate_dependencies', FILE)
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Coordinated fixed runtime/dependency admission required')

    def test_closed_roles_and_immutable_independent_pins(self):
        self.assertEqual(set(P.POLICIES), {'gateway', 'display'})
        self.assertEqual(P.POLICIES['gateway']['count'], 118)
        self.assertEqual(P.POLICIES['display']['count'], 27)
        with self.assertRaises(TypeError):
            P.POLICIES['gateway']['count'] = 1
        for role, raw in (('foreign', b'{}'), ('gateway', b'{}'), ('display', b'x' * (1048576 + 1))):
            with self.assertRaises(ValueError):
                P.dependency_records(role, raw)

    def test_runtime_lf_package_and_lock_contract_is_independent_of_npm_stage_metadata(self):
        root = Path(os.environ['DROIDDECK_CANDIDATE_TEST_INPUTS']) / 'base'
        def read(name):
            return (root / name).read_bytes()
        P.compatible_candidate(read)
        for changed in ('preview-gateway/package.json', 'preview-gateway/package-lock.json',
                        'waydroid-display/package.json', 'waydroid-display/package-lock.json'):
            with self.assertRaises(ValueError):
                P.compatible_candidate(lambda name: b'changed' if name == changed else read(name))

    def test_privilege_and_arguments_before_source_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A.os, 'open', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_inputs(stack, 'fixture', '/root/source', '/home/fixture/gateway', '/home/fixture/display')
        for args in ([], ['--install'], ['--verify-inputs'], ['--verify-inputs', 'fixture', '/root/a', '/home/fixture/b', '/home/fixture/c', '--force']):
            with self.assertRaises(ValueError):
                P.main(args)

    def test_cli_fixed_diagnostics_without_private_arguments(self):
        result = subprocess.run([sys.executable, '-B', str(FILE), '--activate', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime dependency admission refused; no changes performed')

    def test_account_home_alias_and_overlapping_inputs_refuse_before_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')), \
                mock.patch.object(P.os, 'open', side_effect=AssertionError('no reads')):
            for user, gateway, display in (('root', '/home/fixture/a', '/home/fixture/b'),
                                           ('../x', '/home/fixture/a', '/home/fixture/b'),
                                           ('fixture', '/home/other/a', '/home/fixture/b'),
                                           ('fixture', '/home/fixture', '/home/fixture/b'),
                                           ('fixture', '/home/fixture/a', '/home/fixture/a/child'),
                                           ('fixture', '/home//fixture/a', '/home/fixture/b')):
                with self.assertRaises(ValueError):
                    P.hold_runtime_inputs(stack, user, '/root/candidate', gateway, display)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only coordinated input fixtures required')
class CoordinatedInputs(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Coordinated fixed runtime/dependency admission required')
        fixtures.HeldCandidate.setUp(self)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.roots = {}
        account = P.A._account('hilman')
        for role in P.POLICIES:
            source = Path('/home/hilman/droiddeck-' + role + '-dependencies-20261014av')
            destination = self.home / role
            destination.mkdir(mode=0o700)
            with ExitStack() as stack:
                original = P.hold_dependency_bundle(stack, role, source, owner=account[1:3])
                for name in original['files']:
                    target = destination / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target.write_bytes(original['read'](name))
                    target.chmod(0o600)
            for item in [destination, *destination.rglob('*')]:
                item.chmod(0o700 if item.is_dir() else 0o600)
                os.chown(item, 1000, 1000)
            with ExitStack() as stack:
                P.hold_dependency_bundle(stack, role, destination, owner=(1000, 1000))['check']()
            self.roots[role] = destination
        patcher = mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, str(self.home)))
        self.account = patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, stack):
        return P.hold_runtime_inputs(stack, 'fixture', self.source, self.roots['gateway'], self.roots['display'])

    def test_three_closed_inputs_held_together_and_descriptors_close(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = self.hold(stack)
            self.assertTrue(held['policy']['coordinatedInputsVerified'])
            self.assertEqual(held['policy']['dependencyFilesVerified'], 145)
            self.assertFalse(held['policy']['installable'])
            self.assertFalse(held['policy']['servicesActivated'])
            self.assertEqual(held['read']('gateway', 'http-proxy/package.json'), (self.roots['gateway'] / 'http-proxy/package.json').read_bytes())
            held['check']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_swapped_dependency_roles_refuse(self):
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.hold_runtime_inputs(stack, 'fixture', self.source, self.roots['display'], self.roots['gateway'])

    def test_nested_extra_hardlink_and_corrupt_payload_refuse(self):
        root = self.roots['gateway']
        file = root / 'http-proxy/package.json'
        extra = file.with_name('unexpected')
        extra.touch(mode=0o600)
        os.chown(extra, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        extra.unlink()
        os.link(file, self.root / 'linked')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        (self.root / 'linked').unlink()
        file.write_bytes(b'changed')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_original_nested_file_generation_rechecked_after_admission(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            file = self.roots['display'] / 'pngjs/package.json'
            file.rename(self.root / 'retained')
            shutil.copyfile(self.root / 'retained', file)
            file.chmod(0o600)
            os.chown(file, 1000, 1000)
            with self.assertRaises(ValueError):
                held['read']('runtime', 'portable/gpu-access.js')

    def test_runtime_drift_during_other_role_admission_refuses(self):
        original = P.hold_dependency_bundle
        def changed(stack, role, root, **kwargs):
            held = original(stack, role, root, **kwargs)
            if role == 'display':
                (self.source / 'portable/gpu-access.js').write_bytes(b'changed')
            return held
        with mock.patch.object(P, 'hold_dependency_bundle', side_effect=changed), ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_dependency_source_account_drift_invalidates_all_inputs(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.account.return_value = ('fixture', 1001, 1000, str(self.home))
            with self.assertRaises(ValueError):
                held['check']()

    def test_internal_nested_tree_reader_handles_pinned_empty_payload(self):
        root = self.root / 'empty-fixture'
        (root / 'nested').mkdir(mode=0o700, parents=True)
        root.chmod(0o700)
        (root / 'bundle-manifest.json').write_bytes(b'generated fixed recipe')
        (root / 'bundle-manifest.json').chmod(0o600)
        (root / 'nested/empty').touch(mode=0o600)
        def records(raw):
            self.assertEqual(raw, b'generated fixed recipe')
            return {'nested/empty': (0, hashlib.sha256(b'').hexdigest())}
        with ExitStack() as stack:
            held = P.A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=records, manifest_limit=65536)
            self.assertEqual(held['read']('nested/empty'), b'')


if __name__ == '__main__':
    unittest.main()
