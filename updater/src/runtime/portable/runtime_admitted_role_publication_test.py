# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
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

FILE = Path(__file__).with_name('runtime-admitted-role-publication.py')
spec = importlib.util.spec_from_file_location('admitted_role_test', FILE)
spec_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spec_module)
T = spec_module


def fixture():
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': T.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in T.K.L.ARTIFACT_LAYOUT.items():
        source, data = 'artifacts/' + name, ('generated ' + name).encode()
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': T.E._sha(data), 'mode': mode})
    recipes = {name: ('generated ' + name).encode() for name in T.E.RECIPE_FILES}
    raw = T.E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': T.E._records(recipes, recipes),
        'links': [{'path': name, 'target': target} for name, target in sorted(T.K.L.LINKS.items())]}, compact=True)
    layout, readers = T.K._derive(raw)
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
        'read_source': mock.Mock(side_effect=runtime.__getitem__),
        'read_recipe_file': mock.Mock(side_effect=recipes.__getitem__), 'policy': {}}
    with ExitStack() as stack, mock.patch.object(T.K.E, 'hold_owned_install_plan', return_value=upstream):
        held = T.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    held = {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')}}
    payload = {name: (runtime if kind == 'runtime' else recipes)[source]
               for name, (kind, source, _, _) in readers.items()}
    return held, upstream, layout, payload


class Boundary(unittest.TestCase):
    def test_exact_layout_mapping_closed_metadata_no_installed_packaging(self):
        _, _, layout, _ = fixture()
        roles = T._roles(layout)
        self.assertEqual(sum(len(r['files']) for r in roles.values()), 261)
        self.assertEqual(sum(len(r['links']) for r in roles.values()), 4)
        self.assertEqual(set(roles), {'runtime', 'config'})
        self.assertEqual(roles['runtime']['directories'][''], 0o700)
        self.assertEqual(roles['config']['directories'][''], 0o700)
        for role, tree in roles.items():
            manifest = json.loads(tree['manifest'])
            self.assertEqual((manifest['role'], manifest['finalLayoutSha256']), (role, T.E._sha(layout)))
            self.assertFalse(manifest['installable'])
            self.assertFalse(manifest['readyForActivation'])
        self.assertEqual(roles['runtime']['links'], dict(T.K.L.LINKS))

    def test_cli_has_no_install_resume_or_activation(self):
        for args in ([], ['--install'], ['--recover'], ['--publish', 'private-value']):
            with self.assertRaises(ValueError):
                T.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No admitted-role CLI; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated admitted-role fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-owned-publication-', dir='/root')
        self.log = tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.log.cleanup)
        self.parent = Path(self.temp.name)
        self.original, self.upstream, self.layout, self.payload = fixture()
        self.addCleanup(mock.patch.stopall)
        self.admit = mock.patch.object(T.K, 'hold_final_install_layout', return_value=self.original).start()

    def prepare(self, stack):
        return T.prepare_admitted_role_publication(stack, 'u', '/root/source', '/root/recipes',
            self.temp.name, self.log.name, b'{}')

    def test_all_259_original_pairs_261_regular_modes_four_aliases_two_moves(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        writes, chmods = [], []
        write, chmod = T.S._write_private, T.R.os.fchmod
        def written(stack, fd, name, data):
            writes.append((os.readlink('/proc/self/fd/' + str(fd)), name))
            return write(stack, fd, name, data)
        def changed(fd, mode):
            chmods.append(os.readlink('/proc/self/fd/' + str(fd)))
            chmod(fd, mode)
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=written), mock.patch.object(T.R.os, 'fchmod', side_effect=changed):
            with mock.patch.object(T.R.M.subprocess, 'run', side_effect=AssertionError('no artifact or service execution')):
                held = self.prepare(stack)
                self.assertEqual(held['publish_next']()['privateRolesPublished'], 1)
                report = held['publish_next']()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.assertEqual(self.original['read_file'].call_count, 259)
            self.assertEqual(report['privateRolesPublished'], 2)
            self.assertTrue(report['privateRolePayloadsVerified'])
            for flag in (*T.P.J.FALSE_FLAGS, 'payloadAdmissionVerified', 'livePublicationPerformed',
                         'independentSecondTreeObserverVerified', 'crossFilesystemPublicationVerified',
                         'runtimeLinksMaterialized', 'finalPermissionsApplied', 'finalTargetsPublished'):
                self.assertIs(report[flag], False)
            roles = T._roles(self.layout)
            for role, tree in roles.items():
                for name, (size, pin, mode) in tree['files'].items():
                    path = self.parent / role / name
                    data, info = path.read_bytes(), path.lstat()
                    self.assertEqual((len(data), T.E._sha(data)), (size, pin))
                    self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)), (0, 0, 1, mode))
                for name, target in tree['links'].items():
                    self.assertEqual(os.readlink(self.parent / role / name), target)
                self.assertEqual(held['read'](role, T.MANIFEST), tree['manifest'])
                for name, mode in tree['directories'].items():
                    self.assertEqual(stat.S_IMODE((self.parent / role / name).stat().st_mode), mode)
            self.assertEqual(len(writes), 261)
            self.assertEqual(sum(name == T.MANIFEST for _, name in writes), 2)
            self.assertEqual(writes[-1][1], T.MANIFEST)
            self.assertTrue(chmods)
            self.assertTrue(all(name.startswith(self.temp.name + '/.') for name in chmods))
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), report['rolePayloadDescriptorUpperBound'])
            held['observe']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_existing_destination_refuses_before_original_admission(self):
        (self.parent / 'foreign').symlink_to('missing')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.admit.assert_not_called()
        self.assertEqual(list(Path(self.log.name).iterdir()), [])

    def test_disjoint_sources_bounds_and_live_names_refused_before_admission(self):
        for parent, raw in (('/opt/droiddeck', b'{}'), (self.temp.name, b'x' * 16385)):
            with ExitStack() as stack, self.assertRaises(ValueError):
                T.prepare_admitted_role_publication(stack, 'u', '/root/source', '/root/recipes', parent, self.log.name, raw)
        with ExitStack() as stack, self.assertRaises(ValueError):
            T.prepare_admitted_role_publication(stack, 'u', self.temp.name, '/root/recipes', self.temp.name, self.log.name, b'{}')
        self.admit.assert_not_called()

    def test_budget_refuses_before_payload_reads_and_allocation(self):
        with ExitStack() as stack, mock.patch.object(T, '_budget', side_effect=ValueError('generated budget')), self.assertRaises(ValueError):
            self.prepare(stack)
        self.original['read_file'].assert_not_called()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_bad_original_pair_refuses_before_any_allocation(self):
        self.original['read_file'].side_effect = lambda name: b'generated wrong source'
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual(list(Path(self.log.name).iterdir()), [])

    def test_partial_write_preserves_original_allocation_and_no_role_published(self):
        write = T.S._write_private
        calls = []
        def fail(*args):
            calls.append(1)
            if len(calls) == 7:
                raise OSError('generated partial role write')
            return write(*args)
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=fail), self.assertRaises(T.P.PublicationUncertain):
            self.prepare(stack)
        self.assertTrue((self.parent / 'allocation.json').exists())
        self.assertFalse((self.parent / 'runtime').exists())
        self.assertFalse((self.parent / 'config').exists())

    def test_source_drift_during_copy_refuses_before_any_move(self):
        write = T.S._write_private
        def changed(*args):
            result = write(*args)
            self.upstream['check'].side_effect = ValueError('generated original source drift')
            return result
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=changed), self.assertRaises(T.P.PublicationUncertain):
            self.prepare(stack)
        self.assertFalse((self.parent / 'runtime').exists())

    def test_alias_failure_keeps_partial_roles_and_no_chmod_of_existing_parent(self):
        with ExitStack() as stack, mock.patch.object(T.R.os, 'symlink', side_effect=OSError('generated alias failure')), self.assertRaises(T.P.PublicationUncertain):
            self.prepare(stack)
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o700)
        self.assertTrue((self.parent / 'allocation.json').exists())

    def test_after_first_move_source_drift_keeps_runtime_and_second_stage(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            self.upstream['check'].side_effect = ValueError('generated original source drift')
            with self.assertRaises(T.P.PublicationUncertain):
                held['publish_next']()
            self.assertTrue((self.parent / 'runtime').is_dir())
            self.assertFalse((self.parent / 'config').exists())
            with self.assertRaises(T.P.PublicationUncertain):
                held['observe']()

    def test_published_alias_replacement_and_regular_mode_changes_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            name, target = next(iter(T._roles(self.layout)['runtime']['links'].items()))
            alias = self.parent / 'runtime' / name
            alias.unlink()
            alias.symlink_to(target)
            with self.assertRaises(T.P.PublicationUncertain):
                held['observe']()

    def test_regular_mode_change_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            (self.parent / 'runtime' / T.MANIFEST).chmod(0o644)
            with self.assertRaises(T.P.PublicationUncertain):
                held['observe']()

    def test_new_config_payload_stays_below_private_etc_staging_map(self):
        roles = T._roles(self.layout)
        value = json.loads(self.layout)
        for destination in value['registrationTargets']:
            self.assertIn(destination.removeprefix('/etc/'), roles['config']['files'])
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            report = held['publish_next']()
            self.assertFalse(report['servicesRegistered'])
            self.assertEqual(report['lastPhase'], 'config-published')

    def test_low_space_keeps_allocation_without_publishing(self):
        from types import SimpleNamespace
        with ExitStack() as stack, mock.patch.object(T.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)), self.assertRaises(T.P.PublicationUncertain):
            self.prepare(stack)
        self.assertTrue((self.parent / 'allocation.json').exists())
        self.assertFalse((self.parent / 'runtime').exists())

    def test_second_role_write_failure_preserves_first_role_stage(self):
        write = T.S._write_private
        def fail(stack, parent, name, data):
            if '/.config-attempt-' in os.readlink('/proc/self/fd/' + str(parent)):
                raise OSError('generated config role copy failure')
            return write(stack, parent, name, data)
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=fail), self.assertRaises(T.P.PublicationUncertain):
            self.prepare(stack)
        runtime = next(self.parent.glob('.runtime-attempt-*'))
        self.assertTrue((runtime / T.MANIFEST).exists())
        self.assertFalse((self.parent / 'runtime').exists())


if __name__ == '__main__':
    unittest.main()
