# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
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
FILE = HERE / 'runtime-install-plan.py'
spec = importlib.util.spec_from_file_location('placement_plan_tests', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def test_immutable_fixed_layout_and_legacy_artifact_modes(self):
        self.assertEqual(str(P.TARGET), '/opt/droiddeck')
        self.assertEqual(dict(P.ARTIFACT_LAYOUT), {
            'tesla-android-x86-touch': ('guest/bin/tesla-android-x86-touch', 0o700),
            'tesla-android-x86-api': ('guest/bin/tesla-android-x86-api', 0o700),
            'tesla-android-x86-display-latest': ('guest/bin/tesla-android-x86-display-latest', 0o700),
            'location-bridge.jar': ('guest/location-bridge.jar', 0o600),
            'logical-capture.jar': ('guest/logical-capture.jar', 0o600),
            'scrcpy-server-v4.0': ('guest/scrcpy-server-v4.0.jar', 0o600),
            'libglibutil.so.1.0.80': ('libgbinder/libglibutil.so.1.0.80', 0o644),
            'libgbinder.so.1.1.45': ('libgbinder/libgbinder.so.1.1.45', 0o644)})
        self.assertEqual(len(P.LINKS), 4)
        with self.assertRaises(TypeError):
            P.ARTIFACT_LAYOUT['foreign'] = ('outside', 0o777)
        with self.assertRaises(TypeError):
            P.LINKS['foreign'] = '/private-value'

    def test_target_account_matches_existing_runtime_identity_constraints(self):
        with mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')) as account:
            self.assertEqual(P._account('fixture'), account.return_value)
            for value in ('root', '../fixture', None, [], 'BadName'):
                with self.assertRaises(ValueError):
                    P._account(value)
            for identity in (('fixture', 999, 1000, '/home/fixture'), ('fixture', 1000, 999, '/home/fixture'),
                             ('fixture', 1000, 1000, '/home/CON'), ('fixture', 1000, 1000, '/home/com1.name')):
                account.return_value = identity
                with self.assertRaises(ValueError):
                    P._account('fixture')

    def test_root_canonical_and_disjoint_namespace_gate_before_source_reads(self):
        with ExitStack() as stack, mock.patch.object(P.P, 'hold_runtime_artifact_assembly', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.A.sys, 'platform', platform), \
                        mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_install_plan(stack, 'fixture', '/root/source')
            with mock.patch.object(P.A, '_root_linux'):
                for root in ('/', '/root//source', '/opt', '/opt/droiddeck', '/opt/droiddeck/child', '/root/a/../b'):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_install_plan(stack, 'fixture', root)

    def test_held_plan_and_source_reads_recheck_account_and_input_before_return(self):
        held = {'check': mock.Mock(), 'read': mock.Mock(return_value=b'fixed'), 'policy': {}}
        plan = b'{"files":[{"source":"artifacts/location-bridge.jar"}]}\n'
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')) as account, \
                mock.patch.object(P.P, 'hold_runtime_artifact_assembly', return_value=held), \
                mock.patch.object(P, '_plan', return_value=plan):
            result = P.hold_runtime_install_plan(stack, 'fixture', '/root/source')
            held['check'].reset_mock()
            self.assertEqual(result['read_plan'](), plan)
            self.assertEqual(held['check'].call_count, 2)
            for flag in P.FALSE_FLAGS:
                self.assertIs(result['policy'][flag], False)
            for name in ('bundle-manifest.json', '../private-value', '/private-value', [], None):
                with self.assertRaises(ValueError):
                    result['read_source'](name)
            held['check'].side_effect = ValueError('source changed')
            with self.assertRaises(ValueError):
                result['read_plan']()
            held['check'].side_effect = None
            def drift(name):
                account.return_value = ('fixture', 1001, 1000, '/home/fixture')
                return b'fixed'
            held['read'].side_effect = drift
            with self.assertRaises(ValueError):
                result['read_source']('artifacts/location-bridge.jar')
            with self.assertRaises(ValueError):
                result['read_plan']()

    def test_account_change_during_generation_refuses_without_plan_return(self):
        held = {'check': mock.Mock(), 'read': mock.Mock(return_value=b'fixed'), 'policy': {}}
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')) as account, \
                mock.patch.object(P.P, 'hold_runtime_artifact_assembly', return_value=held):
            def changed(*args):
                account.return_value = ('fixture', 1000, 1000, '/home/foreign')
                return b'{"files":[]}'
            with mock.patch.object(P, '_plan', side_effect=changed), self.assertRaises(ValueError):
                P.hold_runtime_install_plan(stack, 'fixture', '/root/source')

    def test_no_install_target_or_pin_override_cli_and_fixed_redacted_failure(self):
        for args in ([], ['--install'], ['--inspect-plan', 'fixture', '/root/a', '--target', '/private-value']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Runtime placement planning refused; no changes performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only placement fixtures required')
class Placement(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='droiddeck-placement-plan-', dir='/root'))
        self.addCleanup(shutil.rmtree, self.root)
        self.source = Path('/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly')
        account = P.A._account('hilman')
        self.identity = ('fixture', *account[1:3], '/home/fixture')
        patcher = mock.patch.object(P.A, '_account', return_value=self.identity)
        self.account = patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, stack):
        return P.hold_runtime_install_plan(stack, 'fixture', self.source)

    def clone(self):
        target = self.root / 'source'
        target.mkdir(mode=0o700)
        with ExitStack() as stack:
            held = P.P.hold_runtime_artifact_assembly(stack, self.source)
            for name in held['files']:
                destination = target / name
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                destination.write_bytes(held['read'](name))
                destination.chmod(0o600)
            held['check']()
        for path in target.rglob('*'):
            if path.is_dir():
                path.chmod(0o700)
        return target

    def test_full_229_file_plan_four_relative_aliases_canonical_bytes_and_fd_cleanup(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = self.hold(stack)
            raw = held['read_plan']()
            value = json.loads(raw)
            self.assertEqual(raw, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), held['policy']['planSha256'])
            self.assertNotIn(str(self.source).encode(), raw)
            self.assertEqual(value['targetAccount'], dict(zip(('user', 'uid', 'gid', 'home'), self.identity)))
            self.assertEqual((value['ownerUid'], value['ownerGid'], value['targetRoot']), (0, 0, '/opt/droiddeck'))
            files = value['files']
            self.assertEqual(len(files), 229)
            self.assertEqual(len({row['source'] for row in files}), 229)
            self.assertEqual(len({row['path'] for row in files}), 229)
            self.assertFalse(P.METADATA.intersection(row['source'] for row in files))
            self.assertEqual(sum(row['path'].startswith('runtime/preview-gateway/node_modules/') for row in files), 118)
            self.assertEqual(sum(row['path'].startswith('runtime/waydroid-display/node_modules/') for row in files), 27)
            directories = {row['path']: row['mode'] for row in value['directories']}
            self.assertEqual((directories[''], directories['guest'], directories['guest/bin'], directories['libgbinder']),
                             (0o755, 0o700, 0o700, 0o755))
            for row in files:
                original = (self.source / row['source']).read_bytes()
                self.assertEqual((row['size'], row['sha256']), (len(original), hashlib.sha256(original).hexdigest()))
                for directory in Path(row['path']).parents:
                    self.assertIn('' if str(directory) == '.' else str(directory), directories)
            paths = {row['path']: row for row in files}
            for name, (path, mode) in P.ARTIFACT_LAYOUT.items():
                self.assertEqual(paths[path]['source'], 'artifacts/' + name)
                self.assertEqual(paths[path]['mode'], mode)
            self.assertEqual(len(value['links']), 4)
            for row in value['links']:
                self.assertNotIn('/', row['target'])
                self.assertIn(str(Path(row['path']).parent / row['target']), paths)
                self.assertNotIn(row['path'], paths)
            for flag in P.FALSE_FLAGS:
                self.assertIs(value[flag], False)
                self.assertIs(held['policy'][flag], False)
            for flag in ('rootPrivateCopyVerified', 'rootPrivateDependencyCopyVerified', 'rootPrivateArtifactCopyVerified'):
                self.assertIs(held['policy'][flag], False)
            self.assertEqual(held['read_source']('artifacts/location-bridge.jar'),
                             (self.source / 'artifacts/location-bridge.jar').read_bytes())
            held['check']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_planning_has_no_write_permissions_or_execution_syscalls(self):
        original, names = os.open, []
        def opened(name, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            names.append(str(name))
            return original(name, flags, *args, **kwargs)
        with ExitStack() as stack:
            for name in ('mkdir', 'write', 'chmod', 'chown', 'symlink', 'unlink'):
                stack.enter_context(mock.patch.object(os, name, side_effect=AssertionError('no writes')))
            stack.enter_context(mock.patch.object(subprocess, 'run', side_effect=AssertionError('no execution')))
            stack.enter_context(mock.patch.object(os, 'open', side_effect=opened))
            held = self.hold(stack)
            held['read_plan']()
            held['read_source']('artifacts/logical-capture.jar')
        self.assertNotIn('opt', names)

    def test_actual_input_generation_change_invalidates_plan_and_payload_reads(self):
        self.source = self.clone()
        with ExitStack() as stack:
            held = self.hold(stack)
            file = self.source / 'artifacts/location-bridge.jar'
            file.rename(self.root / 'retained')
            shutil.copyfile(self.root / 'retained', file)
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                held['read_plan']()
            with self.assertRaises(ValueError):
                held['read_source']('artifacts/logical-capture.jar')

    def test_real_held_input_account_drift_invalidates_cached_plan(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            held['read_plan']()
            self.account.return_value = ('fixture', self.identity[1] + 1, self.identity[2], '/home/fixture')
            with self.assertRaises(ValueError):
                held['read_plan']()
            with self.assertRaises(ValueError):
                held['read_source']('artifacts/logical-capture.jar')

    def test_account_drift_during_real_payload_read_is_rejected_before_return(self):
        original = P.P.hold_runtime_artifact_assembly
        def wrapped(*args):
            held = original(*args)
            def read(name):
                raw = held['read'](name)
                if name == 'artifacts/logical-capture.jar':
                    self.account.return_value = ('fixture', *self.identity[1:3], '/home/foreign')
                return raw
            return {**held, 'read': read}
        with mock.patch.object(P.P, 'hold_runtime_artifact_assembly', side_effect=wrapped), ExitStack() as stack:
            held = self.hold(stack)
            with self.assertRaises(ValueError):
                held['read_source']('artifacts/logical-capture.jar')


if __name__ == '__main__':
    unittest.main()
