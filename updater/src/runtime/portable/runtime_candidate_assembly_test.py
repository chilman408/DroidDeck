# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
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
import runtime_candidate_dependencies_test as fixtures

FILE = Path(__file__).with_name('runtime-candidate-assembly.py')
spec = importlib.util.spec_from_file_location('candidate_assembly', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def test_proof_snapshot_rejects_changed_original_file_generation(self):
        spec = importlib.util.spec_from_file_location('assembly_smoke_test', FILE.with_name('runtime_candidate_assembly_smoke.py'))
        proof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'generated'
            path.write_bytes(b'original fixture')
            snapshot = {(path, True): P.A._identity(path.lstat())}
            proof._check_snapshot(P, snapshot)
            path.write_bytes(b'changed fixture with new size')
            with self.assertRaises(ValueError):
                proof._check_snapshot(P, snapshot)

    def test_proof_dispatch_is_fixed_and_failure_does_not_export_arguments(self):
        result = subprocess.run([sys.executable, '-B', str(FILE.with_name('runtime_candidate_assembly_smoke.py')), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime assembly proof refused; preserve generated proof directories')

    def test_no_copy_install_activation_or_private_argument_cli(self):
        for args in ([], ['--publish', 'private-value'], ['--verify-assembly', '/root/a', '--force']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime assembly verification refused; no changes performed')

    def test_privilege_and_destination_alias_overlap_before_input_reads(self):
        with mock.patch.object(P.D, 'hold_runtime_inputs', side_effect=AssertionError('no reads')), ExitStack() as stack:
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_assembly(stack, 'fixture', '/root/source', '/home/fixture/a', '/home/fixture/b', '/root/output')
            with mock.patch.object(P.A, '_root_linux'):
                for parent in ('/', '/root//output', '/root/source', '/root/source/child', '/home/fixture', '/home/fixture/a/output'):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_assembly(stack, 'fixture', '/root/source', '/home/fixture/a', '/home/fixture/b', parent)

    def test_closed_manifest_and_fixed_internal_publication_names(self):
        for raw in (b'', b'{}', b'x' * (2 * 1024 * 1024 + 1), b'{"version":true}', b'[]'):
            with self.assertRaises(ValueError):
                P.assembly_records(raw)
        self.assertEqual(P.PUBLISHED_NAME, 'assembly')
        with self.assertRaises(ValueError):
            P.S._rename_tree_new(-1, 'attempt', '../foreign')
        with self.assertRaises(ValueError):
            P.S._publish_tree(None, {}, None, published='foreign', rename=None, verify=None)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only assembly fixtures required')
class Assembly(unittest.TestCase):
    def setUp(self):
        fixtures.CoordinatedInputs.setUp(self)
        patch = mock.patch.object(P.A, '_account', self.account)
        patch.start()
        self.addCleanup(patch.stop)
        self.parent = self.root / 'publication'
        self.parent.mkdir(mode=0o700)

    def publish(self, stack):
        return P.publish_runtime_assembly(stack, 'fixture', self.source, self.roots['gateway'], self.roots['display'], self.parent)

    def test_atomic_all_components_independent_inodes_and_post_success_source_isolation(self):
        before = set(os.listdir('/proc/self/fd'))
        original, written = P.S._write_private, []
        def write(stack, directory, name, raw):
            written.append((name, raw))
            return original(stack, directory, name, raw)
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack:
            held = self.publish(stack)
            target = self.parent / 'assembly'
            self.assertEqual(set(self.parent.iterdir()), {target})
            self.assertEqual(len(held['files']), 225)
            self.assertEqual(len(written), 225)
            self.assertEqual(written[-1], ('bundle-manifest.json', (target / 'bundle-manifest.json').read_bytes()))
            self.assertEqual(set(item.name for item in target.iterdir()), {'runtime', 'gateway', 'display', 'bundle-manifest.json'})
            for role, source in (('runtime', self.source), *self.roots.items()):
                for name in (path.relative_to(source) for path in source.rglob('*') if path.is_file()):
                    old, new = source / name, target / role / name
                    self.assertEqual(new.read_bytes(), old.read_bytes())
                    self.assertNotEqual((old.stat().st_dev, old.stat().st_ino), (new.stat().st_dev, new.stat().st_ino))
                    self.assertEqual((new.stat().st_uid, new.stat().st_gid, new.stat().st_mode & 0o777), (0, 0, 0o600))
            for path in (target, *(path for path in target.rglob('*') if path.is_dir())):
                self.assertEqual((path.stat().st_uid, path.stat().st_gid, path.stat().st_mode & 0o777), (0, 0, 0o700))
            for flag in ('sourceAccountMatched', 'rootPrivateCopyVerified', 'rootPrivateDependencyCopyVerified', 'newCoordinatedAssemblyPublished'):
                self.assertTrue(held['policy'][flag])
            for flag in ('sourceFilesInstalled', 'dependencyFilesInstalled', 'dependencyCodeExecuted', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted'):
                self.assertFalse(held['policy'][flag])
            read = held['read']('display/pngjs/package.json')
            (self.roots['display'] / 'pngjs/package.json').write_bytes(b'changed fixture source')
            self.account.return_value = ('fixture', 1001, 1000, str(self.home))
            self.assertEqual(held['read']('display/pngjs/package.json'), read)
            held['check']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_manifest_is_canonical_and_independently_validates_every_component(self):
        components = {role: (source / 'bundle-manifest.json').read_text() for role, source in
                      (('runtime', self.source), *self.roots.items())}
        raw = P._manifest(components)
        records = P.assembly_records(raw)
        self.assertEqual(len(records), 224)
        with self.assertRaises(TypeError):
            records['foreign'] = (0, '')
        variants = [raw + b' ', json.dumps(json.loads(raw), indent=2).encode(),
                    raw.replace(b'"installable":false', b'"installable":true'),
                    raw.replace(b'"version":1', b'"version":true')]
        swapped = dict(components)
        swapped['gateway'], swapped['display'] = swapped['display'], swapped['gateway']
        variants.append(P._manifest(swapped))
        for changed in variants:
            with self.assertRaises(ValueError):
                P.assembly_records(changed)

    def test_corrupt_dependency_and_existing_or_unsafe_destination_refuse_without_writes(self):
        file = self.roots['display'] / 'pngjs/package.json'
        original = file.read_bytes()
        file.write_bytes(b'fixture corruption')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(os.listdir(self.parent), [])
        file.write_bytes(original)
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.parent.chmod(0o700)
        (self.parent / 'assembly').mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(os.listdir(self.parent / 'assembly'), [])

    def test_partial_copy_preserves_attempt_without_top_manifest_and_refuses_retry(self):
        original, count = P.S._write_private, 0
        def write(*args):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('generated partial copy')
            return original(*args)
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        attempt, = self.parent.iterdir()
        self.assertFalse((attempt / 'bundle-manifest.json').exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [attempt])

    def test_account_drift_during_copy_preserves_uncertainty(self):
        original = P.S._write_private
        def write(*args):
            result = original(*args)
            self.account.return_value = ('fixture', 1001, 1000, str(self.home))
            return result
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'assembly').exists())
        self.assertEqual(len(os.listdir(self.parent)), 1)

    def test_account_drift_after_independent_final_verification_is_uncertain(self):
        original = P.hold_runtime_assembly
        def verify(*args):
            result = original(*args)
            self.account.return_value = ('fixture', 1001, 1000, str(self.home))
            return result
        with mock.patch.object(P, 'hold_runtime_assembly', side_effect=verify), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'assembly/bundle-manifest.json').is_file())

    def test_atomic_collision_preserves_foreign_final(self):
        original = P.S._rename_tree_new
        def rename(fd, attempt, name):
            final = self.parent / name
            final.mkdir(mode=0o700)
            (final / 'foreign').write_bytes(b'fixture marker')
            return original(fd, attempt, name)
        with mock.patch.object(P.S, '_rename_tree_new', side_effect=rename), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual((self.parent / 'assembly/foreign').read_bytes(), b'fixture marker')
        self.assertEqual(len(os.listdir(self.parent)), 2)

    def test_post_rename_failure_preserves_original_final_without_success(self):
        original, sync, renamed = P.S._rename_tree_new, os.fsync, False
        def rename(*args):
            nonlocal renamed
            result = original(*args)
            renamed = True
            return result
        def flush(fd):
            if renamed:
                raise OSError('generated final fsync failure')
            return sync(fd)
        with mock.patch.object(P.S, '_rename_tree_new', side_effect=rename), mock.patch.object(P.S.os, 'fsync', side_effect=flush), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'assembly/bundle-manifest.json').exists())

    def test_original_published_nested_generation_is_rechecked(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            file = self.parent / 'assembly/display/pngjs/package.json'
            file.rename(self.root / 'retained')
            shutil.copyfile(self.root / 'retained', file)
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                held['read']('runtime/portable/gpu-access.js')

    def test_original_created_nested_directory_cannot_be_replaced(self):
        original = P.S._write_private
        changed = False
        def write(*args):
            nonlocal changed
            result = original(*args)
            if not changed:
                changed = True
                path = next(self.parent.iterdir()) / 'display/pngjs'
                path.rename(self.root / 'retained-directory')
                path.mkdir(mode=0o700)
            return result
        with mock.patch.object(P.S, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'assembly').exists())

    def test_shared_internal_nested_copy_preserves_pinned_empty_payload(self):
        source = self.root / 'nested-fixture'
        (source / 'one/two').mkdir(mode=0o700, parents=True)
        for path in (source, source / 'one', source / 'one/two'):
            path.chmod(0o700)
        (source / 'bundle-manifest.json').write_bytes(b'fixed generated recipe')
        (source / 'bundle-manifest.json').chmod(0o600)
        (source / 'one/two/empty').touch(mode=0o600)
        def records(raw):
            self.assertEqual(raw, b'fixed generated recipe')
            return {'one/two/empty': (0, hashlib.sha256(b'').hexdigest())}
        def hold(stack, root):
            result = P.A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=records, manifest_limit=65536)
            return {**result, 'policy': {}}
        with ExitStack() as stack:
            package = hold(stack, source)
            result = P.S._publish_tree(stack, package, self.parent, published='assembly',
                rename=lambda fd, name: P.S._rename_tree_new(fd, name, 'assembly'), verify=hold)
            self.assertEqual(result['read']('one/two/empty'), b'')
            result['check']()


if __name__ == '__main__':
    unittest.main()
