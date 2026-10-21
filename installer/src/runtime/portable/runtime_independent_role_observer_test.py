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

FILE = Path(__file__).with_name('runtime-independent-role-observer.py')
spec = importlib.util.spec_from_file_location('independent_role_test', FILE)
O = importlib.util.module_from_spec(spec)
spec.loader.exec_module(O)


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
    held = {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')}}
    return held, upstream


class Boundary(unittest.TestCase):
    def test_closed_allocation_schema_and_no_bool_identity_numbers(self):
        layout, _ = O.K._derive(fixture()[1]['read_plan']())
        from types import SimpleNamespace
        parent = SimpleNamespace(st_dev=1, st_ino=2)
        value = {'version': 1, 'kind': 'private-created-publication-allocation', 'transaction': 'a' * 32,
            'planSha256': O.E._sha(layout), 'parent': {'device': 1, 'inode': 2},
            'roles': [{'role': role, 'createdName': '.' + role + '-attempt-' + 'a' * 32, 'device': 1, 'inode': i}
                      for role, i in zip(O.P.ROLES, (3, 4))],
            'scope': 'private-current-session-only', 'recoveryAuthorized': False}
        raw = O.J._encode(value)
        self.assertEqual(O._allocation(raw, layout, parent), {'runtime': (1, 3), 'config': (1, 4)})
        for bad in (raw + b' ', raw.replace(b'"version":1', b'"version":true'),
                    raw.replace(b'"device":1', b'"device":true'), raw.replace(b'"inode":2', b'"inode":true'),
                    raw.replace(b'"recoveryAuthorized":false', b'"recoveryAuthorized":true'),
                    raw.replace(b'"version":1', b'"extra":1,"version":1'),
                    raw.replace(b'"version":1', b'"version":1,"version":1')):
            with self.assertRaises(ValueError):
                O._allocation(bad, layout, parent)

    def test_no_install_recovery_or_data_copy_cli(self):
        for args in ([], ['--install'], ['--recover'], ['--inspect', 'private-value']):
            with self.assertRaises(ValueError):
                O.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No independent-role CLI; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated independent role fixtures required')
class Observer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-owned-publication-', dir='/root')
        self.log = tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.log.cleanup)
        self.parent, self.journal = Path(self.temp.name), Path(self.log.name) / 'journal'
        created, _ = fixture()
        with ExitStack() as stack, mock.patch.object(O.K, 'hold_final_install_layout', return_value=created):
            held = O.T.prepare_admitted_role_publication(stack, 'u', '/root/source', '/root/recipes', self.temp.name, self.log.name, b'{}')
            held['publish_next']()
            held['publish_next']()
        # Creator FDs are closed. The inspector receives a separately fresh original.
        self.original, self.upstream = fixture()
        self.addCleanup(mock.patch.stopall)
        self.admit = mock.patch.object(O.K, 'hold_final_install_layout', return_value=self.original).start()

    def inspect(self, stack):
        return O.hold_independent_roles(stack, 'u', '/root/source', '/root/recipes', self.temp.name, self.log.name, b'{}')

    def edit_allocation(self, change):
        path = self.parent / 'allocation.json'
        value = json.loads(path.read_bytes())
        change(value)
        path.write_bytes(O.J._encode(value))

    def test_complete_independent_snapshot_fresh_original_once_fd_closure_and_no_authority(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.inspect(stack)
            report = held['observe']()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.original['read_file'].assert_not_called()
            self.assertEqual((report['privateRegularFilesVerified'], report['privateAliasesVerified']), (261, 4))
            self.assertTrue(report['independentRetainedRoleObserverVerified'])
            self.assertTrue(report['readOnlyPrivateInspection'])
            for flag in (*O.J.FALSE_FLAGS, 'storedReceiptAdopted', 'createdInodeOwnershipVerified',
                         'privateTargetOwnershipVerified', 'restartRecoveryVerified', 'crossFilesystemPublicationVerified',
                         'artifactBytesCopied', 'journalAuthenticityVerified', 'journalSuffixCompletenessVerified'):
                self.assertIs(report[flag], False)
            self.assertEqual(report['lastPhase'], 'config-published')
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), report['independentRoleDescriptorUpperBound'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_inspector_opens_read_only_and_never_mutates_or_executes_payload(self):
        opened = O.os.open
        def readonly(name, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            return opened(name, flags, *args, **kwargs)
        with ExitStack() as stack, mock.patch.object(O.os, 'open', side_effect=readonly):
            for name in ('write', 'pwrite', 'mkdir', 'rename', 'unlink', 'rmdir', 'symlink', 'chmod', 'fchmod', 'chown', 'fsync'):
                stack.enter_context(mock.patch.object(O.os, name, side_effect=AssertionError('no mutation')))
            stack.enter_context(mock.patch.object(O.T.R.M.subprocess, 'run', side_effect=AssertionError('no payload execution')))
            self.inspect(stack)['check']()

    def test_altered_private_manifest_rejected_against_fresh_layout(self):
        (self.parent / 'runtime' / O.T.MANIFEST).write_bytes(b'generated wrong manifest')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_role_inode_description_not_adopted(self):
        self.edit_allocation(lambda value: value['roles'][0].update(inode=1))
        previous = '0' * 64
        allocation_sha = O.E._sha((self.parent / 'allocation.json').read_bytes())
        for path in sorted(self.journal.glob('*.json')):
            row = json.loads(path.read_bytes())
            row.update(planSha256=allocation_sha, previousSha256=previous)
            raw = O.J._encode(row)
            path.write_bytes(raw)
            previous = O.J._sha(raw)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_different_layout_digest_refused(self):
        self.edit_allocation(lambda value: value.update(planSha256='c' * 64))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_unknown_parent_entry_preserved(self):
        path = self.parent / '.runtime-attempt-foreign'
        path.mkdir()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)
        self.assertTrue(path.is_dir())

    def test_same_byte_regular_replacement_during_hold_refused(self):
        path = self.parent / 'runtime' / 'runtime' / 'generated-0.js'
        with ExitStack() as stack:
            held = self.inspect(stack)
            raw = path.read_bytes()
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()

    def test_regular_mode_drift_during_hold_refused(self):
        path = self.parent / 'runtime' / O.T.MANIFEST
        with ExitStack() as stack:
            held = self.inspect(stack)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()

    def test_alias_same_target_replacement_during_hold_refused(self):
        name, target = next(iter(O.K.L.LINKS.items()))
        path = self.parent / 'runtime' / name
        with ExitStack() as stack:
            held = self.inspect(stack)
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                held['check']()

    def test_payload_hardlink_refused(self):
        path = self.parent / 'runtime' / O.T.MANIFEST
        link = self.parent.with_name(self.parent.name + '-generated-link')
        os.link(path, link)
        try:
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.inspect(stack)
        finally:
            link.unlink()

    def test_journal_whole_suffix_deletion_never_authorizes_recovery(self):
        (self.journal / '0004.json').unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_consistent_journal_for_different_allocation_refused(self):
        previous = '0' * 64
        for path in sorted(self.journal.glob('*.json')):
            row = json.loads(path.read_bytes())
            row.update(planSha256='c' * 64, previousSha256=previous)
            raw = O.J._encode(row)
            path.write_bytes(raw)
            previous = O.J._sha(raw)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_source_generation_drift_invalidates_retained_observer(self):
        with ExitStack() as stack:
            held = self.inspect(stack)
            self.upstream['check'].side_effect = ValueError('generated source drift')
            with self.assertRaises(ValueError):
                held['check']()

    def test_journal_drift_invalidates_held_observer(self):
        with ExitStack() as stack:
            held = self.inspect(stack)
            (self.journal / '0000.json').chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()

    def test_low_budget_refuses_before_private_content_reads(self):
        with ExitStack() as stack, mock.patch.object(O, '_budget', side_effect=ValueError('generated low budget')):
            with mock.patch.object(O, '_regular', side_effect=AssertionError('no private read')), self.assertRaises(ValueError):
                self.inspect(stack)

    def test_failed_inspection_closes_descriptors_preserves_tree(self):
        initial = set(os.listdir('/proc/self/fd'))
        (self.parent / 'config' / O.T.MANIFEST).write_bytes(b'generated invalid manifest')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertTrue((self.parent / 'runtime').is_dir())
        self.assertTrue((self.parent / 'allocation.json').exists())

    def test_private_role_root_cannot_be_symlink(self):
        path = self.parent / 'runtime'
        moved = self.parent.with_name(self.parent.name + '-generated-runtime')
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=True)
        try:
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.inspect(stack)
        finally:
            path.unlink()
            moved.rename(path)


if __name__ == '__main__':
    unittest.main()
