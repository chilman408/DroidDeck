# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import ast
import importlib.util
import hashlib
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
FILE = HERE / 'runtime-candidate-artifacts.py'
spec = importlib.util.spec_from_file_location('candidate_artifacts', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def test_reproducible_manifest_admission_retains_exact_payload_and_receipt_pins(self):
        # Path-free public hash receipt from the fl/fo reproducible recipe, not binaries.
        receipts = (
            ('touch-native-preview', '2768dc0808115497fb7e53cc9d3be6b28d79e944806dc238e43783c36232e418'),
            ('api-native-preview', 'c39fec51eaa41a1ba4586dd578d6f065d6dbed2b73589a0a2877d40ac74f9dfe'),
            ('diagnostic-display-native-preview', '108d272b59758d45ed2d14aab851985ab8d1deba27b81acdb50219a402d422f3'),
            ('gps-java-dex-preview', 'cff926622f7c4e845f74bf1395c5c4fcb37ad0f28dd9d1e360567be304dac852'),
            ('logical-capture-java-dex-preview', '1ef90fbe878a832e9e69e43658332d8940083141d1b434c93bde7e092b4a70c0'),
            ('binder-shared-library-preview', 'a616bc920edbf18809ed624d6c7c15739e8cee54f554eee47b937cc28f7e647d'))
        value = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-artifact-build-component',
            'installable': False, 'servicesActivated': False, 'androidOperationsExecuted': False,
            'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(P.ARTIFACT_PINS.items())],
            'buildReceipts': [{'component': name, 'sha256': pin} for name, pin in receipts]}
        encode = lambda: (json.dumps(value, indent=2) + '\n').encode('ascii')
        raw = encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '74b302c76b441686b2fab3b001928718c97b3dd77bad52e9a4219560ad037ff2')
        self.assertEqual(P.artifact_records(raw), P.ARTIFACT_PINS)
        for field in ('files', 'buildReceipts'):
            before = value[field][0]['sha256']
            value[field][0]['sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                P.artifact_records(encode())
            value[field][0]['sha256'] = before
        with self.assertRaises(ValueError):
            P.artifact_records(raw + b' ')

    def test_immutable_pins_and_closed_bounded_manifest(self):
        self.assertEqual(len(P.ARTIFACT_PINS), 8)
        self.assertEqual(P.MANIFEST_SHA256, 'fbf7376b8dac11dddadc6a329190167e879b2cf4d459ebdd25b438573fbf4f3e')
        with self.assertRaises(TypeError):
            P.ARTIFACT_PINS['foreign'] = (1, '0' * 64)
        for raw in (None, '', b'', b'{}', b'x' * 65537):
            with self.assertRaises(ValueError):
                P.artifact_records(raw)

    def test_payload_pins_match_old_independent_recipe_without_importing_helper(self):
        tree = ast.parse((HERE / 'stage-images.py').read_text())
        pins = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'ARTIFACT_PINS' for target in node.targets))
        self.assertEqual(dict(P.ARTIFACT_PINS), pins)

    def test_platform_and_uid_refuse_before_file_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A.os, 'open', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.A.sys, 'platform', platform), \
                        mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_runtime_artifact_inputs(stack, 'fixture', '/root/assembly', '/home/fixture/artifacts')

    def test_account_path_and_overlap_refuse_before_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')), \
                mock.patch.object(P.A.os, 'open', side_effect=AssertionError('no reads')):
            for user, assembly, artifacts in (
                    ('root', '/root/a', '/home/fixture/b'), ('../x', '/root/a', '/home/fixture/b'),
                    ('fixture', '/root/a', '/home/other/b'), ('fixture', '/root/a', '/home/fixture'),
                    ('fixture', '/home/fixture/b', '/home/fixture/b'),
                    ('fixture', '/home/fixture/b/a', '/home/fixture/b'),
                    ('fixture', '/home/fixture/b', '/home/fixture/b/a'),
                    ('fixture', '/root//a', '/home/fixture/b'),
                    ('fixture', '/root/a', '/home/fixture/b/../c')):
                with self.assertRaises(ValueError):
                    P.hold_runtime_artifact_inputs(stack, user, assembly, artifacts)

    def test_both_generations_and_account_rechecked_around_every_read(self):
        assembly = {'check': mock.Mock(), 'read': mock.Mock(return_value=b'assembly'), 'policy': {
            **dict.fromkeys(P.W.FALSE_FLAGS, False), 'rootPrivateAssemblyVerified': True}}
        artifacts = {'check': mock.Mock(), 'read': mock.Mock(return_value=b'artifact')}
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', return_value=('fixture', 1000, 1000, '/home/fixture')) as account, \
                mock.patch.object(P.W, 'hold_runtime_assembly', return_value=assembly), \
                mock.patch.object(P, 'hold_artifact_bundle', return_value=artifacts):
            held = P.hold_runtime_artifact_inputs(stack, 'fixture', '/root/assembly', '/home/fixture/artifacts')
            for item in (assembly, artifacts):
                item['check'].reset_mock()
            self.assertEqual(held['read']('artifacts', 'location-bridge.jar'), b'artifact')
            for item in (assembly, artifacts):
                self.assertEqual(item['check'].call_count, 2)
            for flag in (*P.W.FALSE_FLAGS, 'artifactCodeExecuted', 'artifactFilesInstalled', 'rootPrivateArtifactCopyVerified'):
                self.assertIs(held['policy'][flag], False)
            artifacts['check'].side_effect = ValueError('changed artifact')
            with self.assertRaises(ValueError):
                held['read']('assembly', 'runtime/portable/gpu-access.js')
            assembly['read'].assert_not_called()
            artifacts['check'].side_effect = None
            assembly['check'].side_effect = ValueError('changed assembly')
            with self.assertRaises(ValueError):
                held['read']('artifacts', 'logical-capture.jar')
            assembly['check'].side_effect = None
            def drift_during_read(name):
                account.return_value = ('fixture', 1000, 1000, '/home/foreign')
                return b'artifact'
            artifacts['read'].side_effect = drift_during_read
            with self.assertRaises(ValueError):
                held['read']('artifacts', 'logical-capture.jar')
            account.return_value = ('fixture', 1001, 1000, '/home/fixture')
            with self.assertRaises(ValueError):
                held['check']()
            for role in ('runtime', '../artifacts', None, []):
                with self.assertRaises(ValueError):
                    held['read'](role, 'private-value')

    def test_account_drift_during_assembly_admission_prevents_artifact_reads(self):
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.A, '_account', side_effect=[('fixture', 1000, 1000, '/home/fixture'),
                                                             ('fixture', 1001, 1000, '/home/fixture')]), \
                mock.patch.object(P.W, 'hold_runtime_assembly', return_value={}), \
                mock.patch.object(P, 'hold_artifact_bundle', side_effect=AssertionError('no artifact reads')):
            with self.assertRaises(ValueError):
                P.hold_runtime_artifact_inputs(stack, 'fixture', '/root/assembly', '/home/fixture/artifacts')

    def test_cli_has_no_copy_install_or_pin_override_and_redacts_arguments(self):
        for args in ([], ['--install'], ['--verify-inputs', 'fixture', '/root/a'],
                     ['--verify-inputs', 'fixture', '/root/a', '/home/fixture/b', '--force']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--activate', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime artifact admission refused; no changes performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only artifact fixtures required')
class HeldArtifacts(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='droiddeck-artifact-admission-', dir='/root'))
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.source = self.home / 'artifacts'
        self.source.mkdir(mode=0o700)
        account = P.A._account('hilman')
        original = Path('/home/hilman/droiddeck-artifact-build-output-20261015bt/artifacts')
        # Only independently admitted public build files are cloned; never execute them.
        with ExitStack() as stack:
            held = P.hold_artifact_bundle(stack, original, owner=account[1:3])
            self.original = {name: held['read'](name) for name in held['files']}
        for name, raw in self.original.items():
            (self.source / name).write_bytes(raw)
            (self.source / name).chmod(0o600)
            os.chown(self.source / name, account[1], account[2])
        for directory in (self.home, self.source):
            os.chown(directory, account[1], account[2])
        self.assembly = '/root/droiddeck-runtime-assembly-proof-hppfgs2o/assembly'
        self.identity = ('fixture', *account[1:3], str(self.home))
        patcher = mock.patch.object(P.A, '_account', return_value=self.identity)
        self.account = patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, stack):
        return P.hold_runtime_artifact_inputs(stack, 'fixture', self.assembly, self.source)

    def test_all_234_inputs_private_pinned_bytes_and_fd_cleanup(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = self.hold(stack)
            self.assertEqual(held['policy']['inputFilesVerified'], 234)
            self.assertEqual(held['policy']['artifactFilesVerified'], 8)
            for name, raw in self.original.items():
                self.assertEqual(held['read']('artifacts', name), raw)
            self.assertTrue(held['read']('assembly', 'runtime/portable/gpu-access.js'))
            held['check']()
            for flag in (*P.W.FALSE_FLAGS, 'artifactCodeExecuted', 'artifactFilesInstalled', 'rootPrivateArtifactCopyVerified'):
                self.assertIs(held['policy'][flag], False)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_original_artifact_inode_replacement_rejects_even_identical_bytes(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            target = self.source / 'location-bridge.jar'
            target.rename(self.root / 'retained')
            shutil.copyfile(self.root / 'retained', target)
            target.chmod(0o600)
            os.chown(target, *self.identity[1:3])
            with self.assertRaises(ValueError):
                held['read']('assembly', 'runtime/portable/gpu-access.js')

    def test_same_size_artifact_byte_mutation_invalidates_assembly_reads(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            target = self.source / 'logical-capture.jar'
            raw = bytearray(target.read_bytes())
            raw[-1] ^= 1
            target.write_bytes(raw)
            with self.assertRaises(ValueError):
                held['read']('assembly', 'bundle-manifest.json')

    def test_manifest_reformat_or_new_inventory_refuses(self):
        manifest = self.source / P.A.MANIFEST
        manifest.write_bytes(self.original[P.A.MANIFEST] + b'\n')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        manifest.write_bytes(self.original[P.A.MANIFEST])
        target = self.source / 'private-unexpected'
        target.touch(mode=0o600)
        os.chown(target, *self.identity[1:3])
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_modes_owners_symlinks_and_hardlinks_refuse(self):
        target = self.source / 'location-bridge.jar'
        target.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        target.chmod(0o600)
        os.chown(target, 0, 0)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(target, *self.identity[1:3])
        os.link(target, self.root / 'hardlink')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        (self.root / 'hardlink').unlink()
        target.rename(self.root / 'retained')
        target.symlink_to(self.root / 'retained')
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            self.hold(stack)

    def test_original_namespace_generation_rechecked(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            retained = self.home / 'retained'
            self.source.rename(retained)
            shutil.copytree(retained, self.source)
            for path in [self.source, *self.source.iterdir()]:
                os.chown(path, *self.identity[1:3])
            with self.assertRaises(ValueError):
                held['check']()

    def test_account_drift_during_and_after_acquisition_refuses(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.account.return_value = ('fixture', self.identity[1] + 1, self.identity[2], str(self.home))
            with self.assertRaises(ValueError):
                held['check']()
        self.account.return_value = self.identity
        original = P.hold_artifact_bundle
        def changed(*args, **kwargs):
            held = original(*args, **kwargs)
            self.account.return_value = ('fixture', *self.identity[1:3], '/home/foreign')
            return held
        with mock.patch.object(P, 'hold_artifact_bundle', side_effect=changed), ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_artifact_mutation_inside_read_rechecked_before_return(self):
        original = P.hold_artifact_bundle
        def wrapped(*args, **kwargs):
            held = original(*args, **kwargs)
            def changed(name):
                raw = held['read'](name)
                target = self.source / 'logical-capture.jar'
                target.write_bytes(self.original['logical-capture.jar'] + b'changed')
                return raw
            return {**held, 'read': changed}
        with mock.patch.object(P, 'hold_artifact_bundle', side_effect=wrapped), ExitStack() as stack:
            held = self.hold(stack)
            with self.assertRaises(ValueError):
                held['read']('artifacts', 'location-bridge.jar')


if __name__ == '__main__':
    unittest.main()
