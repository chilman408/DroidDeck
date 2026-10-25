# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


U = load('owned_targets', 'runtime-owned-targets.py')
F = load('owned_target_generated_inputs', 'runtime_admitted_split_publication_test.py')
G = load('owned_target_generated_host_packages', 'runtime_host_package_files_test.py')
HOLD_HOST = U._hold_host_namespaces
SUPPORT_RECIPES = {'services/pam.d/droiddeck': U.H.PAM,
    'services/tmpfiles.d/droiddeck.conf': b'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root 1201 -\n'}
ACCOUNT = ('u', 1201, 1201, '/home/u')
CONFIG = dict(zip(U.E.P.C.FIELDS, (1, 'u', 1201, 1201, '192.0.2.10', '/home/u', 8443, 'droiddeck.test')))
OWNED_RECIPES = {**SUPPORT_RECIPES, 'runtime.json': U.E._encode(CONFIG)}


class Boundary(unittest.TestCase):
    def test_canonical_runtime_account_binding_reuses_existing_resolver_and_detects_drift(self):
        with mock.patch.object(U.K.L, '_account', return_value=ACCOUNT) as resolver:
            gid, check = U._bind_support_account(OWNED_RECIPES['runtime.json'], 'u')
            self.assertEqual(gid, 1201)
            check()
            self.assertEqual(resolver.call_args.args, ('u',))
            for changed in (('u', 1202, 1201, '/home/u'), ('u', 1201, 1202, '/home/u'),
                            ('u', 1201, 1201, '/home/changed')):
                resolver.return_value = changed
                with self.assertRaisesRegex(ValueError, 'mapping changed'): check()

    def test_noncanonical_duplicate_extra_and_wrong_typed_runtime_metadata_refuse(self):
        invalid = [b'{}', b'null', json.dumps(CONFIG).encode(), b'x' * 16385,
                   OWNED_RECIPES['runtime.json'].replace(b'"version": 1', b'"version": 1, "version": 1')]
        for key, value in (('uid', True), ('gid', True), ('version', True), ('extra', 'generated')):
            invalid.append(U.E._encode({**CONFIG, key: value}))
        with mock.patch.object(U.K.L, '_account') as resolver:
            for raw in invalid:
                with self.subTest(raw=raw[:60]), self.assertRaises(ValueError): U._bind_support_account(raw, 'u')
            resolver.assert_not_called()

    def test_all_account_identity_fields_must_match_fresh_configuration(self):
        with mock.patch.object(U.K.L, '_account', return_value=ACCOUNT):
            for key, value in (('user', 'other'), ('uid', 1202), ('gid', 1202), ('home', '/home/other')):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'must match'):
                    U._bind_support_account(U.E._encode({**CONFIG, key: value}), 'u')

    def test_support_fixture_preserves_closed_mapping_and_original_hashes(self):
        original, _, layout = F.fixture(SUPPORT_RECIPES)
        trees = U._trees(layout)
        self.assertEqual(sum(len(tree['files']) for tree in trees.values()), 250)
        values = []
        for name, data in SUPPORT_RECIPES.items():
            size, pin, mode = trees['config']['files'][name]
            self.assertEqual((size, pin, mode), (len(data), U.E._sha(data), 0o644))
            values.append(original['read_file']('/etc/droiddeck/' + name))
        U.H._validate_support_recipes(*values)

    def test_all_four_host_guards_held_and_expire_without_writes(self):
        guards = [{'check': mock.Mock()} for _ in range(4)]
        with ExitStack() as stack, mock.patch.object(U.P, 'hold_host_package_files', return_value=guards[0]) as packages, \
                mock.patch.object(U.H, 'hold_support_namespace', return_value=guards[1]) as support, \
                mock.patch.object(U.H.U, 'hold_unit_namespace', return_value=guards[2]) as units, \
                mock.patch.object(U.H.R, 'hold_registration_paths', return_value=guards[3]) as paths:
            check = U._hold_host_namespaces(stack)
            for function in (packages, units, paths): function.assert_called_once_with(stack)
            support.assert_called_once_with(stack, prepared_state=True)
            check()
            for guard in guards: self.assertEqual(guard['check'].call_count, 2)
        with self.assertRaisesRegex(ValueError, 'remain open'): check()

    def test_exact_owned_mapping_excludes_nine_registrations_and_private_metadata(self):
        _, _, layout = F.fixture()
        trees = U._trees(layout)
        self.assertEqual(sum(len(tree['files']) for tree in trees.values()), 250)
        self.assertEqual(sum(len(tree['links']) for tree in trees.values()), 4)
        self.assertEqual(trees['runtime']['links'], dict(U.K.L.LINKS))
        for tree in trees.values():
            self.assertEqual(tree['directories'][''], 0o700)
            self.assertNotIn('role-layout.json', tree['files'])
            self.assertNotIn('final-layout.json', tree['files'])
        self.assertFalse(any(name.startswith(('systemd/', 'pam.d/', 'tmpfiles.d/')) for name in trees['config']['files']))

    def test_fixed_public_api_and_no_cli(self):
        with mock.patch.object(U, '_populate_new_targets', return_value='generated') as populate:
            self.assertEqual(U.populate_new_targets('stack', 'u', 'source', 'recipes', b'{}'), 'generated')
            self.assertEqual(populate.call_args.args[-1], ('/opt', '/etc'))
        with self.assertRaises(ValueError): U.main(['--install'])
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(U.__file__)), '--install'], capture_output=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, b''))


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated owned-target fixtures required')
class Payloads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-target-allocation-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.parents = tuple(self.base / name for name in ('opt', 'etc'))
        for parent in self.parents: parent.mkdir(mode=0o755)
        self.original, _, self.layout = F.fixture(OWNED_RECIPES)
        patch = mock.patch.object(U.K.L, '_account', return_value=ACCOUNT)
        self.account = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(U.K, 'hold_final_install_layout', return_value=self.original)
        self.admit = patch.start()
        self.addCleanup(patch.stop)
        self.namespace = mock.Mock()
        patch = mock.patch.object(U, '_hold_host_namespaces', return_value=self.namespace)
        patch.start()
        self.addCleanup(patch.stop)

    def populate(self, stack):
        return U._populate_new_targets(stack, 'u', '/root/generated-source', '/root/generated-recipes', b'{}', tuple(map(str, self.parents)))

    def empty(self):
        self.assertTrue(all(not list(parent.iterdir()) for parent in self.parents))

    def test_full250_four_aliases_modes_no_registrations_or_packaging_fd_closure(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack, mock.patch.object(U.T.R.M.subprocess, 'run', side_effect=AssertionError('no service or artifact execution')):
            held = self.populate(stack)
            self.assertIs(held['layout'], self.original)
            report = held['observe']()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.assertEqual(self.original['read_file'].call_count, 250)
            self.assertEqual(report['ownedPayloadFilesVerified'], 250)
            self.assertEqual(report['ownedLibraryAliasesVerified'], 4)
            self.assertTrue(report['targetDirectoryOwnershipVerified'])
            self.assertTrue(report['freshOriginalBound'])
            self.assertTrue(report['storageCapacityObserved'])
            self.assertTrue(report['ownedTargetPayloadsVerified'])
            self.assertTrue(report['hostNamespacePreflightObserved'])
            self.assertTrue(report['supportRecipePathsBound'])
            self.assertTrue(report['supportRecipeAccountBound'])
            self.assertTrue(report['hostPackageFileMetadataMatched'])
            self.assertEqual(report['hostPackageFilesObserved'], 3)
            self.assertFalse(report['hostNamespaceReserved'])
            self.assertEqual(report['registrationsWritten'], 0)
            for key in ('privatePackagingInstalled', 'finalPermissionsApplied', 'effectiveHostPolicyVerified',
                        'installationAuthorized', 'servicesRegistered', 'servicesActivated', 'installable',
                        'readyForActivation', 'restartRecoveryVerified', 'persistentStorageVerified',
                        'hostPackagePublisherAuthenticityVerified', 'hostPackageVersionCompatibilityVerified'):
                self.assertFalse(report[key])
            for parent, (role, tree) in zip(self.parents, U._trees(self.layout).items()):
                root = parent / 'droiddeck'
                for name, (size, pin, mode) in tree['files'].items():
                    info, data = (root / name).lstat(), (root / name).read_bytes()
                    self.assertEqual((len(data), U.E._sha(data)), (size, pin))
                    self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, info.st_mode & 0o777), (0, 0, 1, mode))
                for name, target in tree['links'].items(): self.assertEqual(os.readlink(root / name), target)
                for name, mode in tree['directories'].items(): self.assertEqual((root / name).stat().st_mode & 0o777, mode)
                actual = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_file() or path.is_symlink()}
                self.assertEqual(actual, set(tree['files']) | set(tree['links']))
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), report['ownedTargetDescriptorUpperBound'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)
        with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def test_low_capacity_and_invalid_bytes_refuse_before_target_allocation(self):
        with ExitStack() as stack, mock.patch.object(U.X, '_storage_preflight', side_effect=ValueError('generated low capacity')), self.assertRaisesRegex(ValueError, 'low capacity'):
            self.populate(stack)
        self.empty()
        self.original['read_file'].side_effect = lambda name: b'generated corrupt payload'
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'admitted owned payload'):
            self.populate(stack)
        self.empty()

    def test_bad_descriptor_budget_prevents_prefetch_and_targets(self):
        import resource
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(4, 4)), self.assertRaisesRegex(ValueError, 'descriptor'):
            self.populate(stack)
        self.original['read_file'].assert_not_called()
        self.empty()

    def test_existing_target_preserved_and_never_written(self):
        target = self.parents[1] / 'droiddeck'
        target.mkdir()
        (target / 'sentinel').write_bytes(b'generated foreign')
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'never be adopted'):
            self.populate(stack)
        self.assertEqual((target / 'sentinel').read_bytes(), b'generated foreign')
        self.assertEqual(list(self.parents[0].iterdir()), [])

    def test_second_role_failure_preserves_first_payload_and_both_roots(self):
        build = U.T._build_role
        calls = 0
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('generated config failure')
            return build(*args, **kwargs)
        with ExitStack() as stack, mock.patch.object(U.T, '_build_role', side_effect=fail), self.assertRaises(U.C.AllocationUncertain):
            self.populate(stack)
        self.assertTrue(any((self.parents[0] / 'droiddeck').iterdir()))
        self.assertEqual(list((self.parents[1] / 'droiddeck').iterdir()), [])

    def test_observation_byte_drift_poison_context(self):
        with ExitStack() as stack:
            held = self.populate(stack)
            tree = U._trees(self.layout)['runtime']
            name = next(iter(tree['files']))
            (self.parents[0] / 'droiddeck' / name).write_bytes(b'generated mutation')
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def test_alias_replacement_refused(self):
        with ExitStack() as stack:
            held = self.populate(stack)
            name, target = next(iter(U._trees(self.layout)['runtime']['links'].items()))
            alias = self.parents[0] / 'droiddeck' / name
            alias.unlink()
            alias.symlink_to(target)
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def test_input_target_overlap_rejected_before_original_admission(self):
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'disjoint'):
            U._populate_new_targets(stack, 'u', str(self.parents[0] / 'droiddeck'), '/root/generated-recipes', b'{}', tuple(map(str, self.parents)))
        self.admit.assert_not_called()
        self.empty()

    def test_namespace_refusal_prevents_original_admission_and_allocation(self):
        with ExitStack() as stack, mock.patch.object(U, '_hold_host_namespaces', side_effect=ValueError('generated conflict')), self.assertRaisesRegex(ValueError, 'conflict'):
            self.populate(stack)
        self.admit.assert_not_called()
        self.empty()

    def test_unsafe_support_recipes_refuse_before_allocator_even_with_matching_hashes(self):
        pam, tmp = SUPPORT_RECIPES.values()
        changes = ((b'#%PAM-1.0\nauth sufficient pam_permit.so\n', tmp),
                   (pam, tmp.replace(b'/var/lib/droiddeck', b'/var/lib/foreign')),
                   (pam, tmp.replace(b'0700', b'0777')),
                   (pam, tmp.replace(b'1201', b'0')),
                   (pam, tmp + b'd /foreign 0777 root root -\n'))
        for pam_value, tmp_value in changes:
            with self.subTest(pam=pam_value, tmpfiles=tmp_value):
                recipes = {**OWNED_RECIPES, **dict(zip(SUPPORT_RECIPES, (pam_value, tmp_value)))}
                original, _, _ = F.fixture(recipes)
                self.admit.return_value = original
                with ExitStack() as stack, mock.patch.object(U.C, '_allocate_targets') as allocate, self.assertRaisesRegex(ValueError, 'support recipe'):
                    self.populate(stack)
                allocate.assert_not_called()
                self.assertEqual(original['read_file'].call_count, 250)
                self.empty()

    def test_support_recipe_is_checked_from_original_bytes_before_storage(self):
        with ExitStack() as stack, mock.patch.object(U.H, '_validate_support_recipes', wraps=U.H._validate_support_recipes) as validate:
            def storage(*args, **kwargs):
                validate.assert_called_once_with(*SUPPORT_RECIPES.values(), expected_gid=1201)
                self.assertEqual(self.original['read_file'].call_count, 250)
                raise ValueError('generated stop before mkdir')
            with mock.patch.object(U.X, '_storage_preflight', side_effect=storage), self.assertRaisesRegex(ValueError, 'stop before mkdir'):
                self.populate(stack)
        self.empty()

    def test_wrong_rule_group_refuses_before_allocator_with_valid_original_hashes(self):
        recipes = {**OWNED_RECIPES, 'services/tmpfiles.d/droiddeck.conf':
                   SUPPORT_RECIPES['services/tmpfiles.d/droiddeck.conf'].replace(b'1201', b'1202')}
        self.admit.return_value, _, _ = F.fixture(recipes)
        with ExitStack() as stack, mock.patch.object(U.C, '_allocate_targets') as allocate, self.assertRaisesRegex(ValueError, 'runtime account group'):
            self.populate(stack)
        allocate.assert_not_called()
        self.empty()

    def test_account_change_during_storage_preflight_refuses_before_mkdir(self):
        storage = U.X._storage_preflight
        def changed(*args, **kwargs):
            result = storage(*args, **kwargs)
            self.account.return_value = ('u', 1201, 1202, '/home/u')
            return result
        with ExitStack() as stack, mock.patch.object(U.X, '_storage_preflight', side_effect=changed), self.assertRaisesRegex(ValueError, 'account mapping changed'):
            self.populate(stack)
        self.empty()

    def test_account_change_during_first_role_preserves_partial_targets(self):
        build = U.T._build_role
        def changed(*args, **kwargs):
            result = build(*args, **kwargs)
            self.account.return_value = ('u', 1202, 1201, '/home/u')
            return result
        with ExitStack() as stack, mock.patch.object(U.T, '_build_role', side_effect=changed) as builder, self.assertRaises(U.C.AllocationUncertain):
            self.populate(stack)
        self.assertEqual(builder.call_count, 1)
        self.assertTrue(any((self.parents[0] / 'droiddeck').iterdir()))
        self.assertEqual(list((self.parents[1] / 'droiddeck').iterdir()), [])

    def test_late_account_change_poisons_observation_even_after_mapping_restored(self):
        with ExitStack() as stack:
            held = self.populate(stack)
            self.account.return_value = ('u', 1201, 1201, '/home/other')
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()
            self.account.return_value = ACCOUNT
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def test_namespace_drift_during_prefetch_refuses_before_mkdir(self):
        reader = self.original['read_file']
        def read(name):
            value = reader(name)
            self.namespace.side_effect = ValueError('generated drift')
            return value
        self.original['read_file'] = mock.Mock(side_effect=read)
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'drift'):
            self.populate(stack)
        self.empty()

    def test_namespace_drift_during_storage_refuses_before_mkdir(self):
        storage = U.X._storage_preflight
        def changed(*args, **kwargs):
            value = storage(*args, **kwargs)
            self.namespace.side_effect = ValueError('generated storage-time drift')
            return value
        with ExitStack() as stack, mock.patch.object(U.X, '_storage_preflight', side_effect=changed), self.assertRaisesRegex(ValueError, 'storage-time'):
            self.populate(stack)
        self.empty()

    def test_namespace_drift_during_first_role_preserves_partial_targets(self):
        build = U.T._build_role
        def changed(*args, **kwargs):
            value = build(*args, **kwargs)
            self.namespace.side_effect = ValueError('generated copy-time drift')
            return value
        with ExitStack() as stack, mock.patch.object(U.T, '_build_role', side_effect=changed) as builder, self.assertRaises(U.C.AllocationUncertain):
            self.populate(stack)
        self.assertEqual(builder.call_count, 1)
        self.assertTrue(any((self.parents[0] / 'droiddeck').iterdir()))
        self.assertEqual(list((self.parents[1] / 'droiddeck').iterdir()), [])

    def test_namespace_observation_drift_poisons_even_if_conflict_removed(self):
        with ExitStack() as stack:
            held = self.populate(stack)
            self.namespace.side_effect = ValueError('generated late drift')
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()
            self.namespace.side_effect = None
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def host_fixture(self, stack):
        root = self.base / 'host'
        def mapped(path): return PurePosixPath(str(root) + str(path))
        names = ((U.H, 'PAM_DIRS'), (U.H, 'TMP_DIRS'), (U.H, 'STATE_ROOTS'),
                 (U.H.U, 'LOAD_PATHS'), (U.H.R, 'TARGETS'), (U.H.R, 'DROPINS'))
        for module, name in names:
            stack.enter_context(mock.patch.object(module, name, tuple(map(mapped, getattr(module, name)))))
        stack.enter_context(mock.patch.object(U.H.R, 'SYSTEMD', mapped(U.H.R.SYSTEMD)))
        for path in (*U.H.PAM_DIRS, *U.H.TMP_DIRS, *U.H.U.LOAD_PATHS, *U.H.R.DROPINS,
                     *(p.parent for p in (*U.H.STATE_ROOTS, *U.H.R.TARGETS))):
            Path(path).mkdir(parents=True, exist_ok=True, mode=0o755)
        files = {str(root) + path: pair for path, pair in U.P.FILES.items()}
        data, query = G.metadata(files)
        for name, raw in data.items():
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(raw)
            path.chmod(0o644)
        alias = root / 'lib'
        alias.symlink_to('usr/lib')
        stack.enter_context(mock.patch.object(U.P, 'FILES', files))
        stack.enter_context(mock.patch.object(U.P, 'ALIAS', PurePosixPath(alias)))
        stack.enter_context(mock.patch.object(U.P, '_query', side_effect=query))
        stack.enter_context(mock.patch.object(U.H.U, '_manager'))
        stack.enter_context(mock.patch.object(U, '_hold_host_namespaces', wraps=HOLD_HOST))
        return {'file': Path(next(iter(files))), 'data': data, 'query': query, 'alias': alias}

    def test_real_host_package_mismatch_refuses_before_source_or_targets(self):
        with ExitStack() as patches:
            host = self.host_fixture(patches)
            path = host['file']
            original = path.read_bytes()
            path.write_bytes(b'generated package mismatch')
            with ExitStack() as stack, mock.patch.object(U.C, '_allocate_targets') as allocate, self.assertRaises(ValueError):
                self.populate(stack)
            allocate.assert_not_called()
            self.admit.assert_not_called()
            self.empty()
            self.assertEqual(path.read_bytes(), b'generated package mismatch')
            path.write_bytes(original)
            host['query'].owners = b'local diversion from: /generated\n'
            with ExitStack() as stack, self.assertRaises(ValueError): self.populate(stack)
            self.admit.assert_not_called()
            self.empty()

    def test_real_host_package_metadata_drift_during_prefetch_refuses_before_mkdir(self):
        with ExitStack() as patches:
            host = self.host_fixture(patches)
            reader = self.original['read_file']
            def changed(name):
                raw = reader(name)
                host['query'].status = host['query'].status.replace(b'1.2-test', b'1.3-test')
                return raw
            self.original['read_file'] = mock.Mock(side_effect=changed)
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'metadata changed'):
                self.populate(stack)
            self.empty()

    def test_real_host_package_byte_drift_during_copy_preserves_partial_targets(self):
        with ExitStack() as patches:
            host = self.host_fixture(patches)
            build = U.T._build_role
            def changed(*args, **kwargs):
                value = build(*args, **kwargs)
                host['file'].write_bytes(b'generated copy-time host drift')
                return value
            with ExitStack() as stack, mock.patch.object(U.T, '_build_role', side_effect=changed) as builder, self.assertRaises(U.C.AllocationUncertain):
                self.populate(stack)
            self.assertEqual(builder.call_count, 1)
            self.assertTrue(any((self.parents[0] / 'droiddeck').iterdir()))
            self.assertEqual(list((self.parents[1] / 'droiddeck').iterdir()), [])

    def test_real_host_package_alias_replacement_poisons_observation(self):
        with ExitStack() as patches, ExitStack() as stack:
            host = self.host_fixture(patches)
            held = self.populate(stack)
            host['alias'].unlink()
            host['alias'].symlink_to('usr/lib')
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()
            with self.assertRaises(U.C.AllocationUncertain): held['observe']()

    def test_real_composed_namespace_guards_survive_generated_payload_copy(self):
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as patches, ExitStack() as stack:
            self.host_fixture(patches)
            with mock.patch.object(U.H.U.subprocess, 'run', side_effect=AssertionError('no commands')):
                held = self.populate(stack)
                self.assertTrue(held['observe']()['hostNamespacePreflightObserved'])
                conflict = Path(U.H.TMP_DIRS[1]) / 'droiddeck.conf'
                conflict.symlink_to('/dev/null')
                with self.assertRaises(U.C.AllocationUncertain): held['observe']()
                conflict.unlink()
                with self.assertRaises(U.C.AllocationUncertain): held['observe']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_real_namespace_conflicts_prevent_original_admission(self):
        with ExitStack() as patches:
            self.host_fixture(patches)
            conflicts = (Path(U.H.PAM_DIRS[1]) / 'droiddeck', Path(U.H.TMP_DIRS[0]) / 'droiddeck.conf',
                         Path(U.H.STATE_ROOTS[0]),
                         Path(U.H.U.LOAD_PATHS[-1]) / U.H.U.OWNED[0],
                         Path(U.H.U.LOAD_PATHS[-1]) / 'service.d', Path(U.H.R.DROPINS[0]) / 'foreign.conf')
            for path in conflicts:
                with self.subTest(path=str(path)):
                    if path.name == 'service.d':
                        path.mkdir()
                        (path / 'foreign.conf').write_bytes(b'generated')
                    else: path.symlink_to('/absent-generated')
                    with ExitStack() as stack, self.assertRaises(ValueError): self.populate(stack)
                    self.admit.assert_not_called()
                    self.empty()
                    if path.is_dir():
                        (path / 'foreign.conf').unlink()
                        path.rmdir()
                    else: path.unlink()


if __name__ == '__main__':
    unittest.main()
