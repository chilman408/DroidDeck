# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-owned-install-stage.py')
spec = importlib.util.spec_from_file_location('owned_stage_tests', FILE)
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


def fixture():
    runtime, recipe = {}, {name: ('generated ' + name + '\n').encode() for name in F.E.RECIPE_FILES}
    rows = []
    for index in range(229):
        name = 'runtime/source-' + str(index).zfill(3) + '.js'
        source, raw = 'assembly/' + name, ('generated payload ' + str(index) + '\n').encode()
        runtime[source] = raw
        rows.append({'path': name, 'source': source, 'size': len(raw), 'sha256': F.E._sha(raw), 'mode': 0o644})
    value = {'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': F.E._records(recipe, recipe),
        'links': [{'path': 'lib/' + str(i), 'target': 'library.so.1'} for i in range(4)]}
    plan = F.E._encode(value, compact=True)
    return {'check': mock.Mock(), 'read_plan': lambda: plan, 'read_source': runtime.__getitem__,
        'read_recipe_file': recipe.__getitem__, 'policy': {**dict.fromkeys(F.E.P.C.L.FALSE_FLAGS, False),
            'configurationIncluded': True, 'recipeFilesWritten': False,
            'configurationValidatorExecuted': True, 'sourceRecipeCodeExecuted': True}}, value, runtime, recipe


class Boundary(unittest.TestCase):
    def test_exact_materialization_ledger_keeps_links_and_permissions_in_plan_only(self):
        held, _, _, _ = fixture()
        plan = held['read_plan']()
        records, readers = F._layout(plan)
        self.assertEqual((len(records), len(readers)), (251, 250))
        self.assertEqual(records['plan.json'], (len(plan), F.E._sha(plan)))
        manifest = F._manifest(plan, records)
        value = json.loads(manifest)
        self.assertEqual(value['kind'], 'owned-install-candidate')
        self.assertEqual(value['ownedInstallPlanSha256'], F.E._sha(plan))
        self.assertIs(value['finalPermissionsApplied'], False)
        self.assertIs(value['runtimeLinksMaterialized'], False)
        self.assertEqual(len(value['files']), 251)
        self.assertFalse(any('lib/' in name for name in records))
        with self.assertRaises(TypeError):
            records['extra'] = (1, 'a' * 64)

    def test_ambiguous_unsafe_incomplete_or_rewritten_plan_rows_refuse(self):
        _, original, _, _ = fixture()
        for field, value in (('kind', 'machine-install-profile'), ('targetRoot', '/other'), ('installable', True),
                             ('readyForActivation', True), ('files', []), ('links', []), ('ownedRecipeFiles', [])):
            changed = {**original, field: value}
            with self.assertRaises(ValueError):
                F._layout(F.E._encode(changed, compact=True))
        for field, value in (('path', '../outside'), ('path', '/absolute'), ('path', 'runtime/./bad'),
                             ('source', '../outside'), ('size', True), ('sha256', 'bad'), ('extra', 'generated')):
            changed = copy.deepcopy(original)
            changed['files'][0][field] = value
            with self.assertRaises(ValueError):
                F._layout(F.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['files'][0]['path'] = changed['files'][1]['path']
        with self.assertRaises(ValueError):
            F._layout(F.E._encode(changed, compact=True))
        for raw in (None, b'', b'x' * (4 * 1024 * 1024 + 1), b'[]'):
            with self.assertRaises(ValueError):
                F._layout(raw)

    def test_descriptor_preflight_is_conservative_and_never_raises_limits(self):
        resource = SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(24, 24)),
                                   setrlimit=mock.Mock(side_effect=AssertionError('no limit changes')))
        with mock.patch.dict(sys.modules, {'resource': resource}), mock.patch.object(F.os, 'listdir', return_value=['0', '1', '2', '3']):
            result = F._fd_budget(PurePosixPath('/root/stage'), {'plan.json': (10, 'a' * 64)})
            self.assertEqual(result, {'descriptorLimit': 24, 'descriptorUpperBound': 24})
            resource.getrlimit.return_value = (23, 24)
            with self.assertRaisesRegex(ValueError, 'descriptor budget'):
                F._fd_budget(PurePosixPath('/root/stage'), {'plan.json': (10, 'a' * 64)})
            resource.getrlimit.return_value = (-1, -1)
            self.assertEqual(F._fd_budget(PurePosixPath('/root/stage'), {'plan.json': (10, 'a' * 64)})['descriptorLimit'], -1)
            resource.setrlimit.assert_not_called()

    def test_privilege_bounded_metadata_and_private_disjoint_destination_before_source_access(self):
        with ExitStack() as stack, mock.patch.object(F.E, 'hold_owned_install_plan', side_effect=AssertionError('no source')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(F.A.sys, 'platform', platform), mock.patch.object(F.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        F.publish_owned_install_candidate(stack, 'fixture', '/root/source', '/root/recipes', '/root/stage', b'{}')
            with mock.patch.object(F.A, '_root_linux'):
                for parent in ('/opt/droiddeck', '/etc/droiddeck', '/root', '/root/source/sub', '/root/recipes/sub'):
                    with self.assertRaises(ValueError):
                        F.publish_owned_install_candidate(stack, 'fixture', '/root/source', '/root/recipes', parent, b'{}')
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        F.publish_owned_install_candidate(stack, 'fixture', '/root/source', '/root/recipes', '/root/stage', raw)

    def test_cli_has_no_final_install_activation_pin_or_owner_override(self):
        for args in ([], ['--install'], ['--activate'], ['--stage-owned-install', 'fixture', '/root/source', '/root/recipes', '/root/stage', '/metadata'],
                     ['--stage-owned-install', 'fixture', '/root/source', '/root/recipes', '/root/stage', '-', '--owner', 'private-value']):
            with self.assertRaises(ValueError):
                F.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Owned installation staging refused; no installation or activation performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated inactive candidate fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-install-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'output'
        self.parent.mkdir(mode=0o700)
        self.held, self.value, self.runtime, self.recipe = fixture()

    def stage(self, stack):
        with mock.patch.object(F.E, 'hold_owned_install_plan', return_value=self.held) as gate:
            result = F.publish_owned_install_candidate(stack, 'fixture', '/root/source', '/root/recipes', str(self.parent), b'{}')
            gate.assert_called_once()
            return result

    def test_all_252_original_private_files_exact_new_layout_no_links_or_activation(self):
        import resource
        limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptors = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            result = self.stage(stack)
            self.assertEqual(set(self.parent.iterdir()), {self.parent / 'candidate'})
            self.assertEqual(len(result['files']), 252)
            for name, expected in result['expectedRecords'].items():
                raw = result['read'](name)
                self.assertEqual((len(raw), F.E._sha(raw)), expected)
                info = (self.parent / 'candidate' / name).lstat()
                self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, 0o600, 1))
                self.assertTrue(stat.S_ISREG(info.st_mode))
            for target in (self.parent / 'candidate').rglob('*'):
                self.assertFalse(target.is_symlink())
                if target.is_dir():
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            for flag in ('newOwnedInstallCandidatePublished', 'rootPrivateInstallCandidateVerified', 'recipeFilesWritten', 'rootPrivateCopyVerified'):
                self.assertIs(result['policy'][flag], True)
            for flag in ('installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted',
                         'artifactCodeExecuted', 'dependencyCodeExecuted', 'targetNamespaceVerified', 'finalPermissionsApplied', 'runtimeLinksMaterialized'):
                self.assertIs(result['policy'][flag], False)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)
        self.assertEqual(set(os.listdir('/proc/self/fd')), descriptors)

    def test_existing_directory_or_dangling_name_never_replaced_or_cleaned(self):
        candidate = self.parent / 'candidate'
        candidate.mkdir(mode=0o700)
        before = candidate.lstat().st_ino
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.stage(stack)
        self.assertEqual(candidate.lstat().st_ino, before)
        self.assertEqual(set(self.parent.iterdir()), {candidate})
        candidate.rmdir()
        candidate.symlink_to('/nonexistent-generated-test-path')
        before = candidate.lstat().st_ino
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.stage(stack)
        self.assertEqual(candidate.lstat().st_ino, before)
        self.assertTrue(candidate.is_symlink())

    def test_nonprivate_or_linked_parent_refuses_before_attempt(self):
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.stage(stack)
        self.assertEqual(list(self.parent.iterdir()), [])
        self.parent.chmod(0o700)
        actual = self.parent.with_name('actual')
        self.parent.rename(actual)
        self.parent.symlink_to(actual)
        with ExitStack() as stack, self.assertRaises(OSError):
            self.stage(stack)
        self.assertEqual(list(actual.iterdir()), [])

    def test_descriptor_refusal_and_changed_original_bytes_create_nothing(self):
        with mock.patch.object(F, '_fd_budget', side_effect=ValueError('descriptor budget')), ExitStack() as stack, self.assertRaises(ValueError):
            self.stage(stack)
        self.assertEqual(list(self.parent.iterdir()), [])
        source = next(iter(self.runtime))
        self.runtime[source] = b'changed generated bytes'
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'admitted plan'):
            self.stage(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_rename_race_retains_foreign_state_and_unpublished_attempt(self):
        original = F.S._rename_new
        def raced(fd, attempt):
            candidate = self.parent / 'candidate'
            candidate.mkdir(mode=0o700)
            (candidate / 'foreign-marker').write_bytes(b'generated foreign state')
            original(fd, attempt)
        with mock.patch.object(F.S, '_rename_new', side_effect=raced), ExitStack() as stack, self.assertRaises(F.S.CandidatePublicationUncertain):
            self.stage(stack)
        self.assertEqual((self.parent / 'candidate/foreign-marker').read_bytes(), b'generated foreign state')
        self.assertEqual(len(list(self.parent.glob('.candidate-attempt-*'))), 1)

    def test_error_after_successful_rename_preserves_candidate_without_success_claim(self):
        original = F.S._rename_new
        def interrupted(fd, attempt):
            original(fd, attempt)
            raise TimeoutError('generated postrename interruption')
        with mock.patch.object(F.S, '_rename_new', side_effect=interrupted), ExitStack() as stack, self.assertRaises(F.S.CandidatePublicationUncertain):
            self.stage(stack)
        self.assertTrue((self.parent / 'candidate/bundle-manifest.json').is_file())
        self.assertEqual(list(self.parent.glob('.candidate-attempt-*')), [])

    def test_copy_failure_retains_private_partial_state_and_replay_refuses(self):
        original = F.S._write_private
        count = 0
        def interrupted(*args):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('generated write interruption')
            return original(*args)
        with mock.patch.object(F.S, '_write_private', side_effect=interrupted), ExitStack() as stack, self.assertRaises(F.S.CandidatePublicationUncertain):
            self.stage(stack)
        attempts = list(self.parent.glob('.candidate-attempt-*'))
        self.assertEqual(len(attempts), 1)
        before = {str(p): p.stat().st_ino for p in attempts[0].rglob('*')}
        self.assertFalse((attempts[0] / 'bundle-manifest.json').exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.stage(stack)
        self.assertEqual(before, {str(p): p.stat().st_ino for p in attempts[0].rglob('*')})

    def test_source_or_account_drift_after_rename_preserves_candidate_and_refuses(self):
        original = F.S._rename_new
        def changed(fd, attempt):
            original(fd, attempt)
            self.held['check'].side_effect = ValueError('generated source/account drift')
        with mock.patch.object(F.S, '_rename_new', side_effect=changed), ExitStack() as stack, self.assertRaises(F.S.CandidatePublicationUncertain):
            self.stage(stack)
        self.assertTrue((self.parent / 'candidate/bundle-manifest.json').is_file())

    def test_later_same_bytes_inode_replacement_or_account_drift_invalidates_reads(self):
        with ExitStack() as stack:
            result = self.stage(stack)
            self.held['check'].side_effect = ValueError('generated later account drift')
            with self.assertRaises(ValueError):
                result['read']('plan.json')
            self.held['check'].side_effect = None
            name = self.parent / 'candidate/plan.json'
            replacement = Path(self.temp.name) / 'replacement'
            replacement.write_bytes(name.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, name)
            with self.assertRaises(ValueError):
                result['read']('plan.json')

    def test_repaired_candidate_manifest_cannot_change_independently_bound_plan(self):
        with ExitStack() as stack:
            result = self.stage(stack)
            plan = result['read']('plan.json')
        manifest_file = self.parent / 'candidate/bundle-manifest.json'
        manifest = json.loads(manifest_file.read_bytes())
        manifest['ownedInstallPlanSha256'] = 'a' * 64
        manifest_file.write_bytes(F.E._encode(manifest, compact=True))
        with ExitStack() as stack, self.assertRaises(ValueError):
            F._hold_candidate(stack, str(self.parent / 'candidate'), plan)


if __name__ == '__main__':
    unittest.main()
