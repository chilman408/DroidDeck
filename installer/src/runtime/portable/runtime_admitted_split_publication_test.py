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

FILE = Path(__file__).with_name('runtime-admitted-split-publication.py')
spec = importlib.util.spec_from_file_location('admitted_split_test', FILE)
Z = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Z)
T, X = Z.T, Z.X


def fixture(support_recipes=None):
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': Z.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in Z.K.L.ARTIFACT_LAYOUT.items():
        source, data = 'artifacts/' + name, ('generated ' + name).encode()
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': Z.E._sha(data), 'mode': mode})
    recipes = {name: ('generated ' + name).encode() for name in Z.E.RECIPE_FILES}
    if support_recipes is not None:
        recipes.update(support_recipes)
    raw = Z.E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': Z.E._records(recipes, recipes),
        'links': [{'path': name, 'target': target} for name, target in sorted(Z.K.L.LINKS.items())]}, compact=True)
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
        'read_source': mock.Mock(side_effect=runtime.__getitem__),
        'read_recipe_file': mock.Mock(side_effect=recipes.__getitem__), 'policy': {}}
    with ExitStack() as stack, mock.patch.object(Z.K.E, 'hold_owned_install_plan', return_value=upstream):
        original = Z.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    layout = original['read_plan']()
    original = {**original, **{name: mock.Mock(wraps=original[name]) for name in ('check', 'read_plan', 'read_file')}}
    return original, upstream, layout


class Boundary(unittest.TestCase):
    def test_original_mapping_reused_without_live_config_installation(self):
        _, _, raw = fixture()
        roles = T._roles(raw)
        self.assertEqual(sum(len(tree['files']) for tree in roles.values()), 261)
        self.assertEqual(roles['runtime']['links'], dict(Z.K.L.LINKS))
        self.assertEqual(sum(len(tree['links']) for tree in roles.values()), 4)
        for role, tree in roles.items():
            manifest = json.loads(tree['manifest'])
            self.assertEqual((manifest['role'], manifest['finalLayoutSha256']), (role, Z.E._sha(raw)))
            self.assertFalse(manifest['installable'])
            self.assertEqual(tree['directories'][''], 0o700)
        for target in json.loads(raw)['registrationTargets']:
            self.assertIn(target.removeprefix('/etc/'), roles['config']['files'])

    def test_no_mutating_or_recovery_cli(self):
        for args in ([], ['--install'], ['--recover'], ['--publish'], ['--resume', 'private-value']):
            with self.assertRaises(ValueError):
                Z.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No admitted-split CLI; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated admitted-split fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temps = [tempfile.TemporaryDirectory(prefix='droiddeck-split-runtime-ez-', dir='/root'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-split-config-ez-', dir='/run'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-ez-', dir='/root')]
        for entry in self.temps:
            self.addCleanup(entry.cleanup)
        self.parents = dict(zip(X.ROLES, (Path(entry.name) for entry in self.temps[:2])))
        self.log = Path(self.temps[2].name)
        self.assertNotEqual(self.parents['runtime'].stat().st_dev, self.parents['config'].stat().st_dev)
        self.original, self.upstream, self.layout = fixture()
        patch = mock.patch.object(Z.K, 'hold_final_install_layout', return_value=self.original)
        self.admit = patch.start()
        self.addCleanup(patch.stop)

    def prepare(self, stack):
        return Z.prepare_admitted_split_publication(stack, 'u', '/root/source', '/root/recipes',
            str(self.parents['runtime']), str(self.parents['config']), str(self.log), b'{}')

    def assert_empty(self):
        for path in (*self.parents.values(), self.log):
            self.assertEqual(list(path.iterdir()), [])

    def phase(self):
        records = sorted((self.log / 'journal').glob('[0-9][0-9][0-9][0-9].json'))
        return json.loads(records[-1].read_bytes())['phase']

    def test_complete_original_261_four_aliases_modes_two_devices_and_fd_budget(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        changed, writes = [], []
        chmod, write = os.fchmod, T.S._write_private
        def chmod_created(fd, mode):
            changed.append(os.readlink('/proc/self/fd/' + str(fd)))
            chmod(fd, mode)
        def copied(stack, fd, leaf, data):
            writes.append((os.readlink('/proc/self/fd/' + str(fd)), leaf))
            return write(stack, fd, leaf, data)
        with ExitStack() as stack, mock.patch.object(os, 'fchmod', side_effect=chmod_created), mock.patch.object(T.S, '_write_private', side_effect=copied):
            with mock.patch.object(T.R.M.subprocess, 'run', side_effect=AssertionError('no artifact/service execution')):
                held = self.prepare(stack)
                self.assertFalse(held['observe']()['privateCrossDeviceRolePublicationVerified'])
                held['publish_next']()
                report = held['publish_next']()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.assertEqual(self.original['read_file'].call_count, 259)
            self.assertTrue(report['privateAdmittedSplitPayloadsVerified'])
            self.assertTrue(report['privatePayloadSpacePlanSupplied'])
            self.assertTrue(report['privateParentSpacePreflightVerified'])
            self.assertFalse(report['storageCapacityReserved'])
            self.assertTrue(report['privateCrossDeviceRolePublicationVerified'])
            self.assertEqual(report['configStorageScope'], 'ephemeral-private-test-only')
            self.assertEqual(report['lastPhase'], 'config-published')
            for flag in (*X.J.FALSE_FLAGS, 'payloadAdmissionVerified', 'crossFilesystemPublicationVerified',
                         'globallyAtomic', 'persistentConfigDestinationVerified', 'livePublicationPerformed',
                         'registrationAttempted', 'restartRecoveryVerified', 'powerLossRecoveryVerified',
                         'finalTargetsPublished', 'runtimeLinksMaterialized', 'finalPermissionsApplied',
                         'independentSecondTreeObserverVerified'):
                self.assertIs(report[flag], False)
            for role, tree in T._roles(self.layout).items():
                for name, (size, pin, mode) in tree['files'].items():
                    path = self.parents[role] / role / name
                    data, info = path.read_bytes(), path.lstat()
                    self.assertEqual((len(data), Z.E._sha(data)), (size, pin))
                    self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)), (0, 0, 1, mode))
                for name, target in tree['links'].items():
                    self.assertEqual(os.readlink(self.parents[role] / role / name), target)
                for name, mode in tree['directories'].items():
                    self.assertEqual(stat.S_IMODE((self.parents[role] / role / name).stat().st_mode), mode)
                self.assertEqual(held['read'](role, T.MANIFEST), tree['manifest'])
            allocation = (self.parents['runtime'] / 'allocation.json').read_bytes()
            rows = json.loads(allocation)
            self.assertEqual(rows['planSha256'], Z.E._sha(self.layout))
            self.assertNotEqual(*[row['device'] for row in rows['parents']])
            self.assertEqual(json.loads((self.log / 'journal' / '0000.json').read_bytes())['planSha256'], Z.E._sha(allocation))
            self.assertEqual(len(writes), 261)
            for role in X.ROLES:
                own_writes = [leaf for path, leaf in writes if path.startswith(str(self.parents[role]) + '/.')]
                self.assertEqual(own_writes[-1], T.MANIFEST)
            self.assertTrue(changed)
            self.assertTrue(all(any(path.startswith(str(parent) + '/.') for parent in self.parents.values()) for path in changed))
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), report['splitPayloadDescriptorUpperBound'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_existing_config_refuses_before_original(self):
        (self.parents['config'] / 'foreign').symlink_to('missing')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.admit.assert_not_called()
        self.assertEqual(list(self.parents['runtime'].iterdir()), [])
        self.assertEqual(list(self.log.iterdir()), [])

    def test_exact_admitted_role_sizes_and_counts_reach_storage_preflight(self):
        expected = {role: {'sizes': tuple(size for size, _, _ in tree['files'].values()),
            'directories': len(tree['directories']), 'links': len(tree['links'])}
            for role, tree in T._roles(self.layout).items()}
        with ExitStack() as stack, mock.patch.object(X, '_storage_preflight', side_effect=ValueError('generated no capacity')) as storage:
            with self.assertRaisesRegex(ValueError, 'no capacity'):
                self.prepare(stack)
            self.assertEqual(storage.call_args.args[1], expected)
            self.assertEqual(sum(len(row['sizes']) for row in expected.values()), 261)
            self.assert_empty()

    def test_live_names_bounds_and_source_overlap_refused(self):
        runtime, config, log = str(self.parents['runtime']), str(self.parents['config']), str(self.log)
        inputs = [('/root/source', '/opt/droiddeck', config, b'{}'),
                  ('/root/source', runtime, '/etc/droiddeck', b'{}'),
                  ('/root/source', runtime, config, b'x' * 16385),
                  (config, runtime, config, b'{}')]
        for source, first, second, raw in inputs:
            with ExitStack() as stack, self.assertRaises(ValueError):
                Z.prepare_admitted_split_publication(stack, 'u', source, '/root/recipes', first, second, log, raw)
        self.admit.assert_not_called()
        self.assert_empty()

    def test_distinct_device_preflight_before_original_and_writes(self):
        with ExitStack() as stack, mock.patch.object(X, '_different_devices', side_effect=ValueError('generated same device')), self.assertRaises(ValueError):
            self.prepare(stack)
        self.admit.assert_not_called()
        self.assert_empty()

    def test_full_descriptor_budget_includes_extra_parent_chain(self):
        roles = T._roles(self.layout)
        self.assertEqual(Z._budget(roles)['splitPayloadDescriptorUpperBound'], T._budget(roles)['rolePayloadDescriptorUpperBound'] + 3)
        with ExitStack() as stack, mock.patch.object(Z, '_budget', side_effect=ValueError('generated low budget')), self.assertRaises(ValueError):
            self.prepare(stack)
        self.original['read_file'].assert_not_called()
        self.assert_empty()

    def test_bad_original_pair_before_any_allocation(self):
        self.original['read_file'].side_effect = lambda name: b'generated bad pair'
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assert_empty()

    def test_payload_memory_limit_before_reads_or_allocation(self):
        roles = T._roles(self.layout)
        name = next(iter(roles['runtime']['files']))
        _, pin, mode = roles['runtime']['files'][name]
        roles['runtime']['files'][name] = 513 * 1024 * 1024, pin, mode
        with ExitStack() as stack, mock.patch.object(T, '_roles', return_value=roles), self.assertRaises(ValueError):
            self.prepare(stack)
        self.original['read_file'].assert_not_called()
        self.assert_empty()

    def test_config_parent_readmitted_after_original_prefetch(self):
        read = self.original['read_file']._mock_wraps
        def changed(name):
            data = read(name)
            if self.original['read_file'].call_count == 259:
                (self.parents['config'] / 'foreign').write_bytes(b'generated foreign')
            return data
        self.original['read_file'].side_effect = changed
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertEqual(list(self.parents['runtime'].iterdir()), [])
        self.assertEqual(list(self.log.iterdir()), [])
        self.assertEqual((self.parents['config'] / 'foreign').read_bytes(), b'generated foreign')

    def test_config_space_failure_preserves_complete_runtime_stage(self):
        from types import SimpleNamespace
        space = os.fstatvfs
        def low(fd):
            if '/.config-attempt-' in os.readlink('/proc/self/fd/' + str(fd)):
                return SimpleNamespace(f_bavail=0, f_frsize=4096)
            return space(fd)
        with ExitStack() as stack, mock.patch.object(os, 'fstatvfs', side_effect=low), self.assertRaises(X.Uncertain):
            self.prepare(stack)
        runtime = next(self.parents['runtime'].glob('.runtime-attempt-*'))
        self.assertTrue((runtime / T.MANIFEST).is_file())
        self.assertTrue((self.parents['runtime'] / 'allocation.json').exists())
        self.assertFalse((self.parents['runtime'] / 'runtime').exists())
        self.assertEqual(list(self.log.iterdir()), [])

    def test_partial_config_write_preserves_first_stage_and_fd_closure(self):
        initial = set(os.listdir('/proc/self/fd'))
        write = T.S._write_private
        def fail(stack, fd, leaf, data):
            if '/.config-attempt-' in os.readlink('/proc/self/fd/' + str(fd)):
                raise OSError('generated config copy failure')
            return write(stack, fd, leaf, data)
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=fail), self.assertRaises(X.Uncertain):
            self.prepare(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        runtime = next(self.parents['runtime'].glob('.runtime-attempt-*'))
        self.assertTrue((runtime / T.MANIFEST).exists())
        self.assertFalse((self.parents['runtime'] / 'runtime').exists())

    def test_source_drift_during_copy_prevents_any_move(self):
        write = T.S._write_private
        def changed(*args):
            result = write(*args)
            self.upstream['check'].side_effect = ValueError('generated source drift')
            return result
        with ExitStack() as stack, mock.patch.object(T.S, '_write_private', side_effect=changed), self.assertRaises(X.Uncertain):
            self.prepare(stack)
        for role, path in self.parents.items():
            self.assertFalse((path / role).exists())

    def test_source_drift_after_first_move_keeps_runtime_and_config_stage(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            self.upstream['check'].side_effect = ValueError('generated source drift')
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertTrue((self.parents['runtime'] / 'runtime').is_dir())
            self.assertFalse((self.parents['config'] / 'config').exists())
            self.assertTrue(next(self.parents['config'].glob('.config-attempt-*')).is_dir())
            self.assertEqual(self.phase(), 'runtime-published')
            for method in (held['observe'], held['publish_next'], lambda: held['read']('runtime', T.MANIFEST)):
                with self.assertRaises(X.Uncertain):
                    method()

    def test_second_move_collision_preserves_actual_payloads_and_first_inode(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            before = (self.parents['runtime'] / 'runtime').stat()
            rename = X.J._rename_new
            def collide(fd, source, target):
                if target == 'config':
                    (self.parents['config'] / target).symlink_to('missing')
                rename(fd, source, target)
            with mock.patch.object(X.J, '_rename_new', side_effect=collide), self.assertRaises(X.Uncertain):
                held['publish_next']()
            after = (self.parents['runtime'] / 'runtime').stat()
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            config = next(self.parents['config'].glob('.config-attempt-*'))
            self.assertTrue((config / T.MANIFEST).exists())
            self.assertTrue((self.parents['config'] / 'config').is_symlink())
            self.assertEqual(self.phase(), 'config-publish-intent')

    def test_alias_generation_change_in_published_runtime_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            name, target = next(iter(T._roles(self.layout)['runtime']['links'].items()))
            alias = self.parents['runtime'] / 'runtime' / name
            alias.unlink()
            alias.symlink_to(target)
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertEqual(self.phase(), 'runtime-published')

    def test_config_file_mode_drift_on_second_device_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            held['publish_next']()
            (self.parents['config'] / 'config' / T.MANIFEST).chmod(0o644)
            with self.assertRaises(X.Uncertain):
                held['observe']()

    def test_alias_creation_failure_does_not_publish_or_chmod_existing_parents(self):
        with ExitStack() as stack, mock.patch.object(os, 'symlink', side_effect=OSError('generated alias failure')), self.assertRaises(X.Uncertain):
            self.prepare(stack)
        for role, path in self.parents.items():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertFalse((path / role).exists())
        self.assertTrue((self.parents['runtime'] / 'allocation.json').exists())


if __name__ == '__main__':
    unittest.main()
