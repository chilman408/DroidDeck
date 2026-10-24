# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-independent-split-observer.py')
spec = importlib.util.spec_from_file_location('independent_split_test', FILE)
Y = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Y)
O = Y.O
Z = O.T._load('generated_split_creator', 'runtime-admitted-split-publication.py')


def fixture():
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': O.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in O.K.L.ARTIFACT_LAYOUT.items():
        source, data = 'artifacts/' + name, ('generated ' + name).encode()
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': O.E._sha(data), 'mode': mode})
    recipes = {name: ('generated ' + name).encode() for name in O.E.RECIPE_FILES}
    raw = O.E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': O.E._records(recipes, recipes),
        'links': [{'path': name, 'target': target} for name, target in sorted(O.K.L.LINKS.items())]}, compact=True)
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
        'read_source': mock.Mock(side_effect=runtime.__getitem__),
        'read_recipe_file': mock.Mock(side_effect=recipes.__getitem__), 'policy': {}}
    with ExitStack() as stack, mock.patch.object(O.K.E, 'hold_owned_install_plan', return_value=upstream):
        held = O.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    return {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')}}, upstream


class Boundary(unittest.TestCase):
    def setUp(self):
        self.layout = fixture()[0]['read_plan']()
        self.parents = {'runtime': SimpleNamespace(st_dev=1, st_ino=11), 'config': SimpleNamespace(st_dev=2, st_ino=22)}
        self.value = {'version': 1, 'kind': 'private-split-publication-allocation', 'transaction': 'a' * 32,
            'planSha256': O.E._sha(self.layout), 'scope': 'private-current-session-only', 'recoveryAuthorized': False,
            'parents': [{'role': role, 'device': info.st_dev, 'inode': info.st_ino} for role, info in self.parents.items()],
            'roles': [{'role': role, 'createdName': '.' + role + '-attempt-' + 'a' * 32, 'device': device, 'inode': inode}
                      for role, device, inode in (('runtime', 1, 33), ('config', 2, 44))]}

    def test_closed_canonical_split_schema(self):
        raw = O.J._encode(self.value)
        self.assertEqual(Y._allocation(raw, self.layout, self.parents), {'runtime': (1, 33), 'config': (2, 44)})
        for bad in (raw + b' ', raw.replace(b'"version":1', b'"version":true'),
                    raw.replace(b'"device":1', b'"device":true'), raw.replace(b'"inode":22', b'"inode":true'),
                    raw.replace(b'"version":1', b'"extra":1,"version":1'),
                    raw.replace(b'"version":1', b'"version":1,"version":1'),
                    raw.replace(b'"recoveryAuthorized":false', b'"recoveryAuthorized":true'), b'x' * 2049):
            with self.assertRaises(ValueError):
                Y._allocation(bad, self.layout, self.parents)

    def test_wrong_parent_role_order_and_own_device_refused(self):
        for mutate in (lambda row: row['parents'].reverse(), lambda row: row['roles'].reverse(),
                       lambda row: row['parents'][1].update(inode=23),
                       lambda row: row['roles'][1].update(device=1),
                       lambda row: row['roles'][1].update(createdName='../config'),
                       lambda row: row.update(planSha256='b' * 64)):
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.assertRaises(ValueError):
                Y._allocation(O.J._encode(value), self.layout, self.parents)

    def test_no_inspection_install_or_recovery_cli(self):
        for args in ([], ['--inspect'], ['--install'], ['--publish'], ['--recover']):
            with self.assertRaises(ValueError):
                Y.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--inspect'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No independent-split CLI; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated independent split fixtures required')
class Observer(unittest.TestCase):
    def setUp(self):
        self.temps = [tempfile.TemporaryDirectory(prefix='droiddeck-split-runtime-fa-', dir='/root'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-split-config-fa-', dir='/run'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-fa-', dir='/root')]
        for entry in self.temps:
            self.addCleanup(entry.cleanup)
        self.parents = dict(zip(Y.X.ROLES, (Path(entry.name) for entry in self.temps[:2])))
        self.log = Path(self.temps[2].name)
        self.assertNotEqual(self.parents['runtime'].stat().st_dev, self.parents['config'].stat().st_dev)
        created, _ = fixture()
        with ExitStack() as stack, mock.patch.object(Z.K, 'hold_final_install_layout', return_value=created):
            held = Z.prepare_admitted_split_publication(stack, 'u', '/root/source', '/root/recipes',
                str(self.parents['runtime']), str(self.parents['config']), str(self.log), b'{}')
            held['publish_next']()
            held['publish_next']()
        # Creator descriptors are gone; the observer gets a separately fresh original.
        self.original, self.upstream = fixture()
        patch = mock.patch.object(Y.K, 'hold_final_install_layout', return_value=self.original)
        self.admit = patch.start()
        self.addCleanup(patch.stop)

    def inspect(self, stack):
        return Y.hold_independent_split_roles(stack, 'u', '/root/source', '/root/recipes',
            str(self.parents['runtime']), str(self.parents['config']), str(self.log), b'{}')

    def edit_allocation_and_rebind_journal(self, change):
        path = self.parents['runtime'] / 'allocation.json'
        value = json.loads(path.read_bytes())
        change(value)
        raw = O.J._encode(value)
        path.write_bytes(raw)
        previous = '0' * 64
        for entry in sorted((self.log / 'journal').glob('*.json')):
            row = json.loads(entry.read_bytes())
            row.update(planSha256=O.E._sha(raw), previousSha256=previous)
            data = O.J._encode(row)
            entry.write_bytes(data)
            previous = O.J._sha(data)

    def test_full_independent_snapshot_counts_false_authority_fd_lifecycle(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.inspect(stack)
            report = held['observe']()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.original['read_file'].assert_not_called()
            self.assertEqual((report['privateRegularFilesVerified'], report['privateAliasesVerified']), (261, 4))
            self.assertTrue(report['independentSplitRoleObserverVerified'])
            self.assertTrue(report['privateDistinctDevicesObserved'])
            self.assertTrue(report['readOnlyPrivateInspection'])
            for flag in (*O.J.FALSE_FLAGS, 'storedReceiptAdopted', 'createdInodeOwnershipVerified',
                         'privateTargetOwnershipVerified', 'privateCrossDeviceRolePublicationVerified',
                         'crossFilesystemPublicationVerified', 'globallyAtomic', 'persistentConfigDestinationVerified',
                         'concurrentCreatorObserverVerified', 'restartRecoveryVerified', 'powerLossRecoveryVerified',
                         'artifactBytesCopied', 'journalAuthenticityVerified', 'journalSuffixCompletenessVerified'):
                self.assertIs(report[flag], False)
            self.assertEqual(report['lastPhase'], 'config-published')
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), report['independentSplitDescriptorUpperBound'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_observer_never_writes_syncs_copies_publishes_or_executes_artifacts(self):
        opened = os.open
        def readonly(path, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            return opened(path, flags, *args, **kwargs)
        with ExitStack() as stack, mock.patch.object(os, 'open', side_effect=readonly):
            for name in ('write', 'pwrite', 'mkdir', 'rename', 'unlink', 'rmdir', 'symlink', 'chmod', 'fchmod', 'chown', 'fsync'):
                stack.enter_context(mock.patch.object(os, name, side_effect=AssertionError('no mutation')))
            stack.enter_context(mock.patch.object(Y.X, 'prepare_split_publication', side_effect=AssertionError('no publication')))
            stack.enter_context(mock.patch.object(O.T.R.M.subprocess, 'run', side_effect=AssertionError('no artifact execution')))
            self.inspect(stack)['check']()

    def test_config_parent_inode_description_even_rebound_journal_refused(self):
        self.edit_allocation_and_rebind_journal(lambda value: value['parents'][1].update(inode=1))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_config_root_inode_description_even_rebound_journal_refused(self):
        self.edit_allocation_and_rebind_journal(lambda value: value['roles'][1].update(inode=1))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_config_unknown_entry_preserved(self):
        foreign = self.parents['config'] / '.config-attempt-foreign'
        foreign.mkdir()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)
        self.assertTrue(foreign.is_dir())

    def test_config_manifest_must_match_fresh_original(self):
        (self.parents['config'] / 'config' / O.T.MANIFEST).write_bytes(b'generated wrong manifest')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_retained_second_device_regular_replacement_refused(self):
        path = self.parents['config'] / 'config' / O.T.MANIFEST
        with ExitStack() as stack:
            held = self.inspect(stack)
            data = path.read_bytes()
            path.unlink()
            path.write_bytes(data)
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                held['check']()

    def test_second_device_mode_drift_refused(self):
        path = self.parents['config'] / 'config' / O.T.MANIFEST
        with ExitStack() as stack:
            held = self.inspect(stack)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()

    def test_alias_same_target_replacement_refused(self):
        name, target = next(iter(O.K.L.LINKS.items()))
        path = self.parents['runtime'] / 'runtime' / name
        with ExitStack() as stack:
            held = self.inspect(stack)
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                held['check']()

    def test_second_device_hardlink_refused_without_foreign_parent_entry(self):
        path = self.parents['config'] / 'config' / O.T.MANIFEST
        link = self.parents['config'].with_name(self.parents['config'].name + '-generated-link')
        os.link(path, link)
        try:
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.inspect(stack)
        finally:
            link.unlink()

    def test_config_role_symlink_refused(self):
        path = self.parents['config'] / 'config'
        elsewhere = self.parents['config'].with_name(self.parents['config'].name + '-generated-root')
        path.rename(elsewhere)
        path.symlink_to(elsewhere, target_is_directory=True)
        try:
            # Exact outer inventory: rejection must test the symlink itself.
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.inspect(stack)
        finally:
            path.unlink()
            elsewhere.rename(path)

    def test_journal_suffix_deletion_refused_not_recovery(self):
        (self.log / 'journal' / '0004.json').unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_consistent_other_allocation_journal_refused(self):
        previous = '0' * 64
        for path in sorted((self.log / 'journal').glob('*.json')):
            row = json.loads(path.read_bytes())
            row.update(planSha256='b' * 64, previousSha256=previous)
            data = O.J._encode(row)
            path.write_bytes(data)
            previous = O.J._sha(data)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_low_descriptor_budget_before_private_reads(self):
        with ExitStack() as stack, mock.patch.object(Y, '_budget', side_effect=ValueError('generated low budget')):
            with mock.patch.object(O, '_regular', side_effect=AssertionError('no private reads')), self.assertRaises(ValueError):
                self.inspect(stack)

    def test_source_drift_invalidates_retained_snapshot(self):
        with ExitStack() as stack:
            held = self.inspect(stack)
            self.upstream['check'].side_effect = ValueError('generated original drift')
            with self.assertRaises(ValueError):
                held['check']()

    def test_failed_inspection_closes_fds_preserves_both_roles(self):
        initial = set(os.listdir('/proc/self/fd'))
        (self.parents['config'] / 'config' / O.T.MANIFEST).write_bytes(b'generated wrong manifest')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        for role, path in self.parents.items():
            self.assertTrue((path / role).is_dir())


if __name__ == '__main__':
    unittest.main()
