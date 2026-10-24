# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
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
import runtime_candidate_artifacts_test as fixtures

HERE = Path(__file__).parent
FILE = HERE / 'runtime-artifact-assembly.py'
spec = importlib.util.spec_from_file_location('combined_assembly_test', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def test_bounded_closed_recipe_and_fixed_destination(self):
        for raw in (None, '', b'', b'{}', b'[]', b'x' * (4 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                P.assembly_records(raw)
        self.assertEqual(P.PUBLISHED_NAME, 'assembly')
        self.assertEqual(P.ROLES, ('assembly', 'artifacts'))

    def test_privilege_canonical_paths_and_overlap_before_input_reads(self):
        with mock.patch.object(P.X, 'hold_runtime_artifact_inputs', side_effect=AssertionError('no reads')), ExitStack() as stack:
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), \
                        mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_artifact_assembly(stack, 'fixture', '/root/source', '/home/fixture/artifacts', '/root/output')
            with mock.patch.object(P.A, '_root_linux'):
                for parent in ('/', '/root//output', '/root/source', '/root/source/child', '/root',
                               '/home/fixture/artifacts', '/home/fixture/artifacts/child'):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_artifact_assembly(stack, 'fixture', '/root/source', '/home/fixture/artifacts', parent)

    def test_cli_cannot_publish_install_or_override_pins_and_redacts_arguments(self):
        for args in ([], ['--publish', 'private-value'], ['--verify-assembly', '/root/a', '--force']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Combined runtime assembly verification refused; no changes performed')

    def test_proof_dispatch_is_fixed_and_redacted(self):
        helper = HERE / 'runtime_artifact_assembly_smoke.py'
        result = subprocess.run([sys.executable, '-B', str(helper), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Combined runtime proof refused; preserve generated proof directories')

    def test_proof_snapshot_rejects_changed_original_input(self):
        spec = importlib.util.spec_from_file_location('combined_smoke_test', HERE / 'runtime_artifact_assembly_smoke.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'generated'
            path.write_bytes(b'original fixture')
            snapshot = {(path, True): P.A._identity(path.lstat())}
            helper._check_snapshot(P, snapshot)
            path.write_bytes(b'changed fixture')
            with self.assertRaises(ValueError):
                helper._check_snapshot(P, snapshot)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only combined staging fixtures required')
class CombinedAssembly(unittest.TestCase):
    def setUp(self):
        fixtures.HeldArtifacts.setUp(self)
        patcher = mock.patch.object(P.A, '_account', self.account)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.parent = self.root / 'publication'
        self.parent.mkdir(mode=0o700)

    def publish(self, stack):
        return P.publish_runtime_artifact_assembly(stack, 'fixture', self.assembly, self.source, self.parent)

    def test_full_private_copy_manifest_last_provenance_and_completed_isolation(self):
        before = set(os.listdir('/proc/self/fd'))
        original, written = P.S._write_private, []
        def write(*args):
            written.append(args[2])
            return original(*args)
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack:
            held = self.publish(stack)
            target = self.parent / 'assembly'
            self.assertEqual(set(self.parent.iterdir()), {target})
            self.assertEqual(len(held['files']), 235)
            self.assertEqual(len(written), 235)
            self.assertEqual(written[-1], 'bundle-manifest.json')
            self.assertEqual(set(item.name for item in target.iterdir()), {'assembly', 'artifacts', 'bundle-manifest.json'})
            records = P.assembly_records(held['read'](P.A.MANIFEST))
            for name in records:
                role, relative = name.split('/', 1)
                source = (Path(self.assembly) if role == 'assembly' else self.source) / relative
                copied = target / name
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                self.assertNotEqual((source.stat().st_dev, source.stat().st_ino), (copied.stat().st_dev, copied.stat().st_ino))
                self.assertEqual((copied.stat().st_uid, copied.stat().st_gid, copied.stat().st_mode & 0o777, copied.stat().st_nlink), (0, 0, 0o600, 1))
            for directory in (target, *(path for path in target.rglob('*') if path.is_dir())):
                self.assertEqual((directory.stat().st_uid, directory.stat().st_gid, directory.stat().st_mode & 0o777), (0, 0, 0o700))
            for flag in ('rootPrivateCopyVerified', 'rootPrivateDependencyCopyVerified', 'rootPrivateArtifactCopyVerified',
                         'sourceAccountMatched', 'newRuntimeArtifactAssemblyPublished'):
                self.assertTrue(held['policy'][flag])
            for flag in ('sourceFilesInstalled', 'dependencyFilesInstalled', 'artifactFilesInstalled',
                         'dependencyCodeExecuted', 'artifactCodeExecuted', 'installable', 'readyForActivation',
                         'servicesActivated', 'androidOperationsExecuted'):
                self.assertFalse(held['policy'][flag])
            expected = held['read']('artifacts/location-bridge.jar')
            (self.source / 'location-bridge.jar').write_bytes(b'changed fixture source')
            self.account.return_value = ('fixture', self.identity[1] + 1, self.identity[2], str(self.home))
            self.assertEqual(held['read']('artifacts/location-bridge.jar'), expected)
            held['check']()
            file = target / 'artifacts/location-bridge.jar'
            file.rename(self.root / 'retained-copy')
            shutil.copyfile(self.root / 'retained-copy', file)
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                held['check']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_closed_canonical_recipe_independently_validates_both_components(self):
        components = {'assembly': (Path(self.assembly) / P.A.MANIFEST).read_text(),
                      'artifacts': (self.source / P.A.MANIFEST).read_text()}
        raw = P._manifest(components)
        records = P.assembly_records(raw)
        self.assertEqual(len(records), 234)
        with self.assertRaises(TypeError):
            records['foreign'] = (1, '0' * 64)
        variants = [raw + b' ', json.dumps(json.loads(raw), indent=2).encode(),
                    raw.replace(b'"version":1', b'"version":true'),
                    raw.replace(b'"installable":false', b'"installable":true')]
        for role in components:
            changed = dict(components)
            changed[role] += ' '
            variants.append(P._manifest(changed))
        variants.append(P._manifest({'assembly': components['artifacts'], 'artifacts': components['assembly']}))
        for changed in variants:
            with self.assertRaises(ValueError):
                P.assembly_records(changed)

    def test_corrupt_input_or_unsafe_nonempty_destination_refuses_before_writes(self):
        file = self.source / 'location-bridge.jar'
        file.write_bytes(b'fixture corruption')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])
        file.write_bytes(self.original['location-bridge.jar'])
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.parent.chmod(0o700)
        (self.parent / 'assembly').mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list((self.parent / 'assembly').iterdir()), [])

    def test_partial_copy_preserved_no_top_manifest_or_retry(self):
        original, count = P.S._write_private, 0
        def write(*args):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('generated write failure')
            return original(*args)
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        attempt, = self.parent.iterdir()
        self.assertFalse((attempt / P.A.MANIFEST).exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [attempt])

    def test_input_or_account_drift_during_copy_preserves_uncertainty(self):
        original = P.S._write_private
        def write(*args):
            result = original(*args)
            (self.source / 'logical-capture.jar').write_bytes(b'changed fixture')
            return result
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'assembly').exists())
        self.assertEqual(len(list(self.parent.iterdir())), 1)

    def test_account_drift_after_independent_final_verifier_is_uncertain(self):
        original = P.hold_runtime_artifact_assembly
        def verify(*args):
            result = original(*args)
            self.account.return_value = ('fixture', self.identity[1] + 1, self.identity[2], str(self.home))
            return result
        with mock.patch.object(P, 'hold_runtime_artifact_assembly', side_effect=verify), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'assembly/bundle-manifest.json').is_file())

    def test_account_drift_during_copy_preserves_attempt_without_success(self):
        original = P.S._write_private
        def write(*args):
            result = original(*args)
            self.account.return_value = ('fixture', self.identity[1] + 1, self.identity[2], str(self.home))
            return result
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'assembly').exists())
        self.assertEqual(len(list(self.parent.iterdir())), 1)

    def test_collision_preserves_foreign_destination_without_replace(self):
        original = P.S._rename_tree_new
        def rename(fd, attempt, name):
            final = self.parent / name
            final.mkdir(mode=0o700)
            (final / 'foreign').write_bytes(b'generated marker')
            return original(fd, attempt, name)
        with mock.patch.object(P.S, '_rename_tree_new', side_effect=rename), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual((self.parent / 'assembly/foreign').read_bytes(), b'generated marker')
        self.assertEqual(len(list(self.parent.iterdir())), 2)

    def test_post_rename_fsync_failure_preserves_final_without_success(self):
        original, sync, renamed = P.S._rename_tree_new, os.fsync, False
        def rename(*args):
            nonlocal renamed
            result = original(*args)
            renamed = True
            return result
        def flush(fd):
            if renamed:
                raise OSError('generated post-rename fsync failure')
            return sync(fd)
        with mock.patch.object(P.S, '_rename_tree_new', side_effect=rename), \
                mock.patch.object(P.S.os, 'fsync', side_effect=flush), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'assembly/bundle-manifest.json').is_file())


if __name__ == '__main__':
    unittest.main()
