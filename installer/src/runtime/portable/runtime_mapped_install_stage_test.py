# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-mapped-install-stage.py')
spec = importlib.util.spec_from_file_location('mapped_install_stage_tests', FILE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def fixture():
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': M.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in M.K.L.ARTIFACT_LAYOUT.items():
        data, source = ('generated ' + name).encode(), 'artifacts/' + name
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': M.E._sha(data), 'mode': mode})
    recipe = {name: ('generated ' + name).encode() for name in M.E.RECIPE_FILES}
    original = {'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': M.E._records(recipe, recipe),
        'links': [{'path': name, 'target': target} for name, target in sorted(M.K.L.LINKS.items())]}
    plan = M.E._encode(original, compact=True)
    layout, readers = M.K._derive(plan)
    files = {name: (runtime if kind == 'runtime' else recipe)[source] for name, (kind, source, _, _) in readers.items()}
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=plan),
        'read_source': mock.Mock(side_effect=runtime.__getitem__),
        'read_recipe_file': mock.Mock(side_effect=recipe.__getitem__),
        'policy': {'configurationValidatorExecuted': True, 'sourceRecipeCodeExecuted': True}}
    with ExitStack() as stack, mock.patch.object(M.K.E, 'hold_owned_install_plan', return_value=upstream):
        held = M.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    held = {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')},
            '_original': upstream}
    return held, files


class Boundary(unittest.TestCase):
    def test_real_original_hold_rejects_generation_drift_without_reading_final_plan(self):
        held, _ = fixture()
        original = held['_original']
        original['read_plan'].return_value = b'changed'
        with self.assertRaisesRegex(ValueError, 'Original final placement authority changed'):
            held['check']()
        held['read_plan'].assert_not_called()

    def test_real_original_hold_keeps_identity_and_guarded_source_bytes_checks(self):
        held, files = fixture()
        original = held['_original']
        original['check'].side_effect = ValueError('generated original identity drift')
        with self.assertRaisesRegex(ValueError, 'identity drift'):
            held['check']()
        original['check'].side_effect = None
        original['read_source'].side_effect = lambda _: b'changed'
        destination = next(name for name in files if name.startswith('/opt/droiddeck/runtime/'))
        with self.assertRaisesRegex(ValueError, 'Final placement source differs'):
            held['read_file'](destination)

    def test_exact_mapped_inventory_preserves_original_manifests_and_unapplied_modes(self):
        held, _ = fixture()
        layout = held['read_plan']()
        records, readers = M._layout(layout)
        self.assertEqual((len(records), len(readers)), (260, 259))
        self.assertIn('etc/droiddeck/bundle-manifest.json', records)
        self.assertIn('opt/droiddeck/pid-compat/portable/pid-startup.js', records)
        self.assertIn('opt/droiddeck/headless-owned-runtime/policy.json', records)
        self.assertEqual(sum(name.startswith('etc/') and '/droiddeck/' not in name for name in readers), 9)
        self.assertNotIn('bundle-manifest.json', records)
        self.assertFalse(any('/recipe/' in name for name in records))
        manifest = json.loads(M._manifest(layout, records))
        self.assertEqual(manifest['finalLayoutSha256'], M.E._sha(layout))
        self.assertIs(manifest['finalPermissionsApplied'], False)
        self.assertIs(manifest['runtimeLinksMaterialized'], False)
        for name in M.K.L.LINKS:
            self.assertNotIn('opt/droiddeck/' + name, records)
        with self.assertRaises(TypeError):
            readers['foreign'] = '/foreign'

    def test_canonical_closed_bounds_paths_modes_and_topology_refuse(self):
        held, _ = fixture()
        original = json.loads(held['read_plan']())
        for key, value in (('version', True), ('kind', 'foreign'), ('files', []), ('links', []),
                           ('registrationTargets', []), ('installable', True)):
            with self.assertRaises(ValueError):
                M._layout(M.E._encode({**original, key: value}, compact=True))
        for key, value in (('path', '/etc/foreign'), ('path', '/opt/droiddeck/../foreign'),
                           ('path', '/opt//droiddeck/file'), ('uid', True), ('gid', 1000), ('mode', 0o777),
                           ('registration', 0), ('size', True), ('sha256', 'bad'), ('stagedPath', '../outside')):
            changed = copy.deepcopy(original)
            changed['files'][0][key] = value
            with self.assertRaises(ValueError):
                M._layout(M.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['files'][0] = changed['files'][1]
        with self.assertRaises(ValueError):
            M._layout(M.E._encode(changed, compact=True))
        for raw in (None, b'', b'[]', b'x' * (4 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                M._layout(raw)

    def test_privilege_paths_bounds_and_redacted_closed_cli(self):
        with ExitStack() as stack, mock.patch.object(M, '_preflight_parent', side_effect=AssertionError('no filesystem')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(M.A.sys, 'platform', platform), mock.patch.object(M.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        M.publish_mapped_install_candidate(stack, 'u', '/root/s', '/root/r', '/root/p', b'{}')
            with mock.patch.object(M.A, '_root_linux'):
                for parent in ('/opt/droiddeck', '/etc/droiddeck', '/root', '/root/s/sub', '/root/r/sub'):
                    with self.assertRaises(ValueError):
                        M.publish_mapped_install_candidate(stack, 'u', '/root/s', '/root/r', parent, b'{}')
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        M.publish_mapped_install_candidate(stack, 'u', '/root/s', '/root/r', '/root/p', raw)
        for args in ([], ['--install'], ['--recover'], ['--stage-mapped-install', 'u', '/s', '/r', '/p', '-', '--owner', '0']):
            with self.assertRaises(ValueError):
                M.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Mapped candidate staging refused; no installation or activation performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated mapped payload fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-mapped-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'stage'
        self.parent.mkdir(mode=0o700)
        self.held, self.files = fixture()
        self.addCleanup(mock.patch.stopall)
        self.admit = mock.patch.object(M.K, 'hold_final_install_layout', return_value=self.held).start()

    def publish(self, stack):
        return M.publish_mapped_install_candidate(stack, 'u', '/root/source', '/root/recipes', str(self.parent), b'{}')

    def test_all_261_private_files_exact_mapping_single_original_hold_fd_closure(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.publish(stack)
            self.admit.assert_called_once()
            self.assertEqual(self.held['read_file'].call_count, 259)
            self.held['read_plan'].assert_called_once_with()
            root = self.parent / 'candidate'
            paths = list(root.rglob('*'))
            self.assertEqual(sum(p.is_file() for p in paths), 261)
            for path in [root, *paths]:
                info = path.lstat()
                self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)),
                                 (0, 0, 0o700 if path.is_dir() else 0o600))
                self.assertFalse(path.is_symlink())
            for destination, data in self.files.items():
                self.assertEqual((root / destination[1:]).read_bytes(), data)
            expected_layout = M.K._derive(self.held['_original']['read_plan']())[0]
            self.assertEqual(held['read']('final-layout.json'), expected_layout)
            self.held['read_plan'].assert_called_once_with()
            for flag in ('finalPermissionsApplied', 'runtimeLinksMaterialized', 'finalTargetsPublished',
                         'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized'):
                self.assertIs(held['policy'][flag], False)
            self.assertIs(held['policy']['parentPreflightContinuouslyHeld'], False)
            self.assertIs(held['policy']['publisherParentIndependentlyReadmitted'], True)
            self.assertIs(held['policy']['newMappedCandidatePublished'], True)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_existing_parent_entries_refuse_before_original_admission(self):
        for kind in ('file', 'directory', 'symlink'):
            path = self.parent / 'foreign'
            if kind == 'file':
                path.write_bytes(b'generated')
            elif kind == 'directory':
                path.mkdir()
            else:
                path.symlink_to('absent')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.publish(stack)
            self.admit.assert_not_called()
            path.rmdir() if kind == 'directory' else path.unlink()

    def test_parent_mode_owner_symlink_and_budget_refuse_without_copies(self):
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.parent.chmod(0o700)
        os.chown(self.parent, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        os.chown(self.parent, 0, 0)
        with ExitStack() as stack, mock.patch.object(M.F, '_fd_budget', side_effect=ValueError('generated')):
            with self.assertRaises(ValueError):
                self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_original_byte_mismatch_refuses_before_any_attempt(self):
        self.held['read_file'].side_effect = lambda _: b'changed'
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_exclusive_rename_race_preserves_foreign_target_and_attempt(self):
        original = M.S._rename_new
        def race(fd, attempt):
            (self.parent / 'candidate').mkdir(mode=0o700)
            (self.parent / 'candidate' / 'foreign').write_bytes(b'generated')
            original(fd, attempt)
        with ExitStack() as stack, mock.patch.object(M.S, '_rename_new', side_effect=race), self.assertRaises(M.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual((self.parent / 'candidate' / 'foreign').read_bytes(), b'generated')
        self.assertEqual(len(list(self.parent.glob('.candidate-attempt-*'))), 1)

    def test_postrename_verifier_failure_preserves_candidate_without_retry(self):
        with ExitStack() as stack, mock.patch.object(M, '_hold_candidate', side_effect=ValueError('generated')), self.assertRaises(M.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'candidate').is_dir())
        self.assertFalse(list(self.parent.glob('.candidate-attempt-*')))

    def test_partial_write_failure_preserves_private_attempt(self):
        original = M.S._write_private
        def write(*args):
            original(*args)
            raise OSError('generated interruption')
        with ExitStack() as stack, mock.patch.object(M.S, '_write_private', side_effect=write), self.assertRaises(M.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual(len(list(self.parent.glob('.candidate-attempt-*'))), 1)
        self.assertFalse((self.parent / 'candidate').exists())

    def test_later_same_byte_inode_replacement_is_uncertain(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            path = self.parent / 'candidate' / 'etc/droiddeck/runtime.json'
            path.rename(path.with_name('old'))
            path.write_bytes(path.with_name('old').read_bytes())
            path.chmod(0o600)
            with self.assertRaises(M.S.CandidatePublicationUncertain):
                held['check']()

    def test_changed_candidate_cannot_be_adopted_with_repaired_manifest(self):
        with ExitStack() as stack:
            self.publish(stack)
        root = self.parent / 'candidate'
        path = root / 'etc/droiddeck/runtime.json'
        path.write_bytes(b'changed')
        manifest = json.loads((root / M.A.MANIFEST).read_bytes())
        row = next(row for row in manifest['files'] if row['path'] == 'etc/droiddeck/runtime.json')
        row['size'], row['sha256'] = 7, M.E._sha(b'changed')
        (root / M.A.MANIFEST).write_bytes(M.E._encode(manifest, compact=True))
        with ExitStack() as stack, self.assertRaises(ValueError):
            M._hold_candidate(stack, root, self.held['read_plan']())

    def test_source_generation_change_after_rename_preserves_candidate(self):
        original = M.S._rename_new
        def rename(fd, attempt):
            original(fd, attempt)
            self.held['_original']['read_plan'].return_value = b'changed'
        with ExitStack() as stack, mock.patch.object(M.S, '_rename_new', side_effect=rename), self.assertRaises(M.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'candidate').is_dir())

    def test_original_generation_drift_during_prefetch_refuses_before_attempt(self):
        read = self.held['read_file']._mock_wraps
        def changed(destination):
            data = read(destination)
            self.held['_original']['read_plan'].return_value = b'changed'
            return data
        self.held['read_file'].side_effect = changed
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'Original final placement authority changed'):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_original_generation_drift_after_publication_refuses_returned_reads(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            self.held['_original']['read_plan'].return_value = b'changed'
            with self.assertRaises(M.S.CandidatePublicationUncertain):
                held['read']('final-layout.json')
        self.assertTrue((self.parent / 'candidate').is_dir())

    def test_original_generation_drift_during_copy_preserves_private_attempt(self):
        write = M.S._write_private
        def changed(*args):
            result = write(*args)
            self.held['_original']['read_plan'].return_value = b'changed'
            return result
        with ExitStack() as stack, mock.patch.object(M.S, '_write_private', side_effect=changed):
            with self.assertRaises(M.S.CandidatePublicationUncertain):
                self.publish(stack)
        self.assertEqual(len(list(self.parent.glob('.candidate-attempt-*'))), 1)
        self.assertFalse((self.parent / 'candidate').exists())

    def test_parent_changed_during_source_admission_is_freshly_refused(self):
        def admit(*args):
            (self.parent / 'foreign').write_bytes(b'generated')
            return self.held
        self.admit.side_effect = admit
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual([p.name for p in self.parent.iterdir()], ['foreign'])

    def test_later_permission_or_hardlink_change_is_uncertain(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            path = self.parent / 'candidate' / 'etc/droiddeck/runtime.json'
            path.chmod(0o644)
            with self.assertRaises(M.S.CandidatePublicationUncertain):
                held['check']()
            path.chmod(0o600)
            extra = self.parent / 'foreign-hardlink'
            os.link(path, extra)
            with self.assertRaises(M.S.CandidatePublicationUncertain):
                held['check']()


if __name__ == '__main__':
    unittest.main()
