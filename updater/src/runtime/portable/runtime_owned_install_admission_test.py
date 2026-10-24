# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('retained_admission_tests', Path(__file__).with_name('runtime-owned-install-admission.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
fixture_spec = importlib.util.spec_from_file_location('retained_fixture', Path(__file__).with_name('runtime_owned_install_stage_test.py'))
T = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(T)


class Boundary(unittest.TestCase):
    def test_invalid_cli_has_no_recovery_install_pin_or_owner_override(self):
        for argv in ([], ['--install'], ['--recover'], ['--inspect-retained-candidate', 'u', '/s', '/r', '/root/p', '/settings'],
                     ['--inspect-retained-candidate', 'u', '/s', '/r', '/root/p', '-', '--expected-plan', 'secret']):
            with self.assertRaises(ValueError):
                G.main(argv)
        result = subprocess.run([sys.executable, '-B', G.__file__, '--recover', 'secret'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Retained installation candidate refused; no installation or recovery performed')

    def test_root_linux_required_before_input_or_parent_access(self):
        with ExitStack() as stack, mock.patch.object(G, '_hold_parent', side_effect=AssertionError('no parent')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(G.A.sys, 'platform', platform), mock.patch.object(G.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        G.hold_owned_install_candidate(stack, 'u', '/root/s', '/root/r', '/root/p', b'{}')

    def test_private_canonical_disjoint_names_and_bounded_settings(self):
        with ExitStack() as stack, mock.patch.object(G.A, '_root_linux'), mock.patch.object(G, '_hold_parent', side_effect=AssertionError('no parent')):
            for parent in ('/root', '/opt/droiddeck', '/etc/droiddeck', '/root/../p', '/root/p/', '/root/s/sub', '/root/r/sub'):
                with self.assertRaises(ValueError):
                    G.hold_owned_install_candidate(stack, 'u', '/root/s', '/root/r', parent, b'{}')
            for raw in (None, b'', b'x' * 16385):
                with self.assertRaises(ValueError):
                    G.hold_owned_install_candidate(stack, 'u', '/root/s', '/root/r', '/root/p', raw)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated retained candidate fixtures required')
class Retained(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-retained-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'retained'
        self.parent.mkdir(mode=0o700)
        self.root = self.parent / 'candidate'
        self.root.mkdir(mode=0o700)
        self.held, self.value, runtime, recipes = T.fixture()
        self.plan = self.held['read_plan']()
        records, readers = G.F._layout(self.plan)
        self.expected = {**records}
        files = {'plan.json': self.plan, G.A.MANIFEST: G.F._manifest(self.plan, records)}
        for name, (role, source) in readers.items():
            files[name] = runtime[source] if role == 'runtime' else recipes[source]
        self.files = files
        for name, raw in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # mkdir(parents=True) follows umask for intermediate directories.
            for parent in path.parents:
                if parent == self.root:
                    break
                parent.chmod(0o700)
            path.write_bytes(raw)
            path.chmod(0o600)
        self.gate = mock.patch.object(G.E, 'hold_owned_install_plan', return_value=self.held).start()
        self.addCleanup(mock.patch.stopall)

    def admit(self, stack):
        return G.hold_owned_install_candidate(stack, 'fixture', '/root/source', '/root/recipes', str(self.parent), b'{}')

    def test_complete_reopening_all_pairs_no_writes_execution_copy_or_install_claim(self):
        import resource
        initial = set(os.listdir('/proc/self/fd'))
        limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        before = {str(p): G.A._identity(p.lstat()) for p in (self.parent, *self.parent.rglob('*'))}
        with ExitStack() as stack, mock.patch.object(G.S, '_publish_tree', side_effect=AssertionError('no publisher')):
            result = self.admit(stack)
            self.gate.assert_called_once()
            self.assertEqual(len(result['files']), 252)
            self.assertEqual(result['read_plan'](), self.plan)
            for name, expected in result['expectedRecords'].items():
                raw = result['read'](name)
                self.assertEqual((len(raw), G.E._sha(raw)), expected)
            for flag in ('retainedCandidateVerified', 'candidateBoundToFreshInputs', 'rootPrivateInstallCandidateVerified', 'candidateParentInventoryVerified'):
                self.assertIs(result['policy'][flag], True)
            for flag in ('newOwnedInstallCandidatePublished', 'rootPrivateCopyVerified', 'rootPrivateDependencyCopyVerified',
                         'rootPrivateArtifactCopyVerified', 'recipeFilesWritten', 'finalPermissionsApplied', 'runtimeLinksMaterialized',
                         'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation', 'servicesActivated', 'artifactCodeExecuted'):
                self.assertIs(result['policy'][flag], False)
            with self.assertRaises(TypeError):
                result['expectedRecords']['foreign'] = (0, 'a' * 64)
            for name in ('../outside', 'opt', 'private-settings', None):
                with self.assertRaises(ValueError):
                    result['read'](name)
        self.assertEqual(before, {str(p): G.A._identity(p.lstat()) for p in (self.parent, *self.parent.rglob('*'))})
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)

    def test_empty_partial_or_ambiguous_parent_never_adopted_or_changed(self):
        self.root.rename(self.parent / '.candidate-attempt-generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        self.gate.assert_not_called()
        (self.parent / '.candidate-attempt-generated').rename(self.root)
        extra = self.parent / '.candidate-attempt-foreign'
        extra.mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        self.assertTrue(extra.is_dir())
        self.gate.assert_not_called()
        extra.rmdir()
        (self.root / 'plan.json').unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        self.assertFalse((self.root / 'plan.json').exists())

    def test_repaired_embedded_plan_and_manifest_not_independent_authority(self):
        value = json.loads(self.plan)
        value['files'][0]['sha256'] = 'b' * 64
        plan = G.E._encode(value, compact=True)
        records, _ = G.F._layout(plan)
        (self.root / 'plan.json').write_bytes(plan)
        (self.root / G.A.MANIFEST).write_bytes(G.F._manifest(plan, records))
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'independently held'):
            self.admit(stack)

    def test_fresh_original_plan_change_refuses_unchanged_candidate(self):
        value = json.loads(self.plan)
        value['configurationDigest'] = 'c' * 64
        self.held['read_plan'] = lambda: G.E._encode(value, compact=True)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)

    def test_wrong_parent_mode_owner_or_symlink_refuses(self):
        for mode in (0o755, 0o770):
            self.parent.chmod(mode)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.admit(stack)
        self.parent.chmod(0o700)
        os.chown(self.parent, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        os.chown(self.parent, 0, 0)
        actual = self.parent.with_name('actual')
        self.parent.rename(actual)
        self.parent.symlink_to(actual)
        with ExitStack() as stack, self.assertRaises(OSError):
            self.admit(stack)

    def test_nonprivate_linked_or_extra_candidate_payload_refuses(self):
        name = next(name for name in self.files if name.startswith('opt/'))
        path = self.root / name
        path.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        path.chmod(0o600)
        alias = self.parent.with_name('hardlink')
        os.link(path, alias)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        alias.unlink()
        extra = self.root / 'extra'
        extra.write_bytes(b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        extra.unlink()
        path.unlink()
        path.symlink_to('/nonexistent-generated-only')
        with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
            self.admit(stack)

    def test_same_bytes_replaced_original_file_invalidates_all_reads(self):
        with ExitStack() as stack:
            result = self.admit(stack)
            path = self.root / 'plan.json'
            original = path.with_name('original')
            path.rename(original)
            path.write_bytes(self.plan)
            path.chmod(0o600)
            original.unlink()
            for read in (result['check'], result['read_plan'], lambda: result['read']('plan.json')):
                with self.assertRaises(ValueError):
                    read()

    def test_source_account_drift_after_admission_invalidates_all_reads(self):
        with ExitStack() as stack:
            result = self.admit(stack)
            self.held['check'].side_effect = ValueError('generated account or source drift')
            for read in (result['check'], result['read_plan'], lambda: result['read']('plan.json')):
                with self.assertRaises(ValueError):
                    read()

    def test_parent_replacement_or_new_attempt_after_admission_refuses(self):
        with ExitStack() as stack:
            result = self.admit(stack)
            extra = self.parent / '.candidate-attempt-foreign'
            extra.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                result['read_plan']()
            extra.rmdir()
            original = self.parent.with_name('original')
            self.parent.rename(original)
            self.parent.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                result['read']('plan.json')

    def test_descriptor_refusal_or_generation_change_during_admission_closes_everything(self):
        initial = set(os.listdir('/proc/self/fd'))
        with mock.patch.object(G.F, '_fd_budget', side_effect=ValueError('descriptor budget')), ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        original = G.F._hold_candidate
        def drift(*args):
            result = original(*args)
            self.held['check'].side_effect = ValueError('generated drift during admission')
            return result
        with mock.patch.object(G.F, '_hold_candidate', side_effect=drift), ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)


if __name__ == '__main__':
    unittest.main()
