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

FILE = Path(__file__).with_name('runtime-prepared-tree.py')
spec = importlib.util.spec_from_file_location('prepared_tree_tests', FILE)
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


def fixture():
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': T.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in T.K.L.ARTIFACT_LAYOUT.items():
        source, data = 'artifacts/' + name, ('generated ' + name).encode()
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': T.E._sha(data), 'mode': mode})
    recipe = {name: ('generated ' + name).encode() for name in T.E.RECIPE_FILES}
    raw = T.E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': T.E._records(recipe, recipe),
        'links': [{'path': name, 'target': target} for name, target in sorted(T.K.L.LINKS.items())]}, compact=True)
    layout, readers = T.K._derive(raw)
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
        'read_source': mock.Mock(side_effect=runtime.__getitem__), 'read_recipe_file': mock.Mock(side_effect=recipe.__getitem__), 'policy': {}}
    with ExitStack() as stack, mock.patch.object(T.K.E, 'hold_owned_install_plan', return_value=upstream):
        held = T.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    held = {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')}}
    payload = {name[1:]: (runtime if kind == 'runtime' else recipe)[source]
               for name, (kind, source, _, _) in readers.items()}
    tree = T._spec(layout)
    payload.update({'final-layout.json': layout, T.A.MANIFEST: tree['manifest']})
    return held, upstream, tree, payload


class Boundary(unittest.TestCase):
    def test_exact_261_modes_four_direct_aliases_private_metadata_and_frozen_spec(self):
        _, _, tree, _ = fixture()
        self.assertEqual((len(tree['files']), len(tree['links'])), (261, 4))
        self.assertEqual(tree['files']['opt/droiddeck/guest/bin/tesla-android-x86-touch'][2], 0o700)
        self.assertEqual(tree['files']['opt/droiddeck/guest/logical-capture.jar'][2], 0o600)
        self.assertEqual(tree['files']['etc/droiddeck/runtime.json'][2], 0o644)
        self.assertEqual(tree['directories']['opt/droiddeck/guest/bin'], 0o700)
        self.assertEqual(tree['directories']['opt/droiddeck'], 0o755)
        self.assertEqual(tree['directories'][''], 0o700)
        self.assertEqual(sum(n.startswith('etc/') and not n.startswith('etc/droiddeck/') for n in tree['files']), 9)
        for name, target in tree['links'].items():
            self.assertEqual(PurePosixPath(target).name, target)
            self.assertIn(str(PurePosixPath(name).parent / target), tree['files'])
        for mapping in ('files', 'directories', 'links'):
            with self.assertRaises(TypeError):
                tree[mapping]['foreign'] = None
        manifest = json.loads(tree['manifest'])
        self.assertEqual((len(manifest['files']), len(manifest['links'])), (260, 4))
        self.assertFalse(manifest['installable'])

    def test_canonical_layout_owned_modes_and_alias_policy_refuse(self):
        _, _, _, payload = fixture()
        original = json.loads(payload['final-layout.json'])
        for key, value in (('ownedDirectories', []), ('links', []), ('installable', True)):
            with self.assertRaises(ValueError):
                T._spec(T.E._encode({**original, key: value}, compact=True))
        for key, value in (('uid', False), ('gid', 1000), ('mode', 0o777), ('path', '/etc/foreign')):
            changed = copy.deepcopy(original)
            changed['ownedDirectories'][0][key] = value
            with self.assertRaises(ValueError):
                T._spec(T.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['links'][0]['target'] = '/outside'
        with self.assertRaises(ValueError):
            T._spec(T.E._encode(changed, compact=True))
        with self.assertRaises(ValueError):
            T._spec(b' ' + payload['final-layout.json'])

    def test_budget_counts_link_descriptors_and_never_changes_limits(self):
        _, _, tree, _ = fixture()
        resource = SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(10000, 10000)))
        root = PurePosixPath('/root/generated/prepared')
        with mock.patch.dict(sys.modules, resource=resource), mock.patch.object(T.os, 'listdir', return_value=['0', '1', '2']):
            expected = 3 + len(root.parts) + len(tree['directories']) - 1 + 261 + 4 + 8
            self.assertEqual(T._budget(root, tree)['preparedVerifierDescriptorUpperBound'], expected)
            resource.getrlimit.return_value = (expected - 1, expected - 1)
            with self.assertRaises(ValueError):
                T._budget(root, tree)

    def test_root_paths_disjoint_settings_and_closed_redacted_cli(self):
        with ExitStack() as stack, mock.patch.object(T.K, 'hold_final_install_layout', side_effect=AssertionError('no admission')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(T.A.sys, 'platform', platform), mock.patch.object(T.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        T.hold_prepared_install_tree(stack, 'u', '/root/s', '/root/r', '/root/p', b'{}')
            with mock.patch.object(T.A, '_root_linux'):
                for parent in ('/root', '/opt/droiddeck', '/etc/droiddeck', '/root/s/sub', '/root/r/sub', '/root//bad'):
                    with self.assertRaises(ValueError):
                        T.hold_prepared_install_tree(stack, 'u', '/root/s', '/root/r', parent, b'{}')
                with self.assertRaises(ValueError):
                    T.hold_prepared_install_tree(stack, 'u', '/root/s', '/root/r', '/root/p', b'x' * 16385)
        for args in ([], ['--install'], ['--recover'], ['--inspect-prepared-tree', 'u', '/s', '/r', '/p', '-', '--mode', '755']):
            with self.assertRaises(ValueError):
                T.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Prepared tree inspection refused; no installation or activation performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated prepared-tree fixtures required')
class Observation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-prepared-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'prepared'
        self.original, self.upstream, self.tree, self.payload = fixture()
        for name, mode in sorted(self.tree['directories'].items(), key=lambda item: (item[0].count('/'), item[0])):
            (self.root / name).mkdir(mode=mode)
            (self.root / name).chmod(mode)
        for name, data in self.payload.items():
            path = self.root / name
            path.write_bytes(data)
            path.chmod(self.tree['files'][name][2])
        for name, target in self.tree['links'].items():
            (self.root / name).symlink_to(target)
        self.addCleanup(mock.patch.stopall)
        self.admit = mock.patch.object(T.K, 'hold_final_install_layout', return_value=self.original).start()
        self.file = self.root / 'etc/droiddeck/runtime.json'
        self.alias = self.root / next(iter(self.tree['links']))

    def hold(self, stack):
        return T.hold_prepared_install_tree(stack, 'u', '/root/source', '/root/recipes', str(self.parent), b'{}')

    def snapshot(self):
        return {str(p.relative_to(self.parent)): T.A._identity(p.lstat()) for p in (self.parent, *self.parent.rglob('*'))}

    def test_all_261_files_four_aliases_original_hold_readonly_and_fd_closure(self):
        import resource
        initial, limit, before = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE), self.snapshot()
        with ExitStack() as stack, ExitStack() as denied:
            for name in ('write', 'pwrite', 'chmod', 'fchmod', 'chown', 'fchown', 'mkdir', 'symlink', 'unlink', 'rename', 'fsync'):
                denied.enter_context(mock.patch.object(T.os, name, side_effect=AssertionError('read only')))
            denied.enter_context(mock.patch.object(T.M.subprocess, 'run', side_effect=AssertionError('no service/artifact execution')))
            opened = denied.enter_context(mock.patch.object(T.os, 'open', wraps=T.os.open))
            held = self.hold(stack)
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.assertEqual((len(held['files']), len(held['links'])), (261, 4))
            for name, data in self.payload.items():
                self.assertEqual(held['read'](name), data)
            for name, target in self.tree['links'].items():
                self.assertEqual(held['read_link'](name), target)
            for flag in ('createdInodeOwnershipVerified', 'finalPermissionsApplied', 'runtimeLinksMaterialized', 'finalTargetsPublished',
                         'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation'):
                self.assertIs(held['policy'][flag], False)
            self.assertIs(held['policy']['preparedFileModesVerified'], True)
            self.assertIs(held['policy']['preparedLibraryAliasesVerified'], True)
            self.original['read_file'].assert_not_called()
            for call in opened.call_args_list:
                flags = call.args[1]
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)

    def test_private_packaging_modes_are_not_final_modes(self):
        for path in self.root.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_missing_or_extra_inventory_refuses(self):
        (self.root / 'foreign').write_bytes(b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        (self.root / 'foreign').unlink()
        self.file.unlink()
        with ExitStack() as stack, self.assertRaises(OSError):
            self.hold(stack)

    def test_parent_extra_child_and_nonprivate_modes_refuse(self):
        (self.parent / 'foreign').mkdir()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        (self.parent / 'foreign').rmdir()
        for path in (self.parent, self.root):
            path.chmod(0o755)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            path.chmod(0o700)

    def test_symlinked_root_or_interior_directory_refuses_without_traversal(self):
        for path in (self.root, self.root / 'opt/droiddeck/runtime'):
            saved = path.with_name(path.name + '-saved')
            path.rename(saved)
            path.symlink_to(saved.name, target_is_directory=True)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            path.unlink()
            saved.rename(path)

    def test_regular_owner_mode_hardlink_and_symlink_refuse(self):
        self.file.chmod(0o666)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.file.chmod(0o644)
        os.chown(self.file, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(self.file, 0, 0)
        with tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-link-', dir='/root') as external:
            os.link(self.file, Path(external) / 'hardlink')
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'single-link prepared regular'):
                self.hold(stack)
        self.file.unlink()
        self.file.symlink_to('/etc/passwd')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_alias_absolute_escape_chain_dangling_and_regular_substitution_refuse(self):
        for target in ('/etc/passwd', '../foreign', 'libglibutil.so', 'absent'):
            self.alias.unlink()
            self.alias.symlink_to(target)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
        self.alias.unlink()
        self.alias.write_bytes(b'generated')
        self.alias.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_alias_owner_and_hardlink_refuse(self):
        os.lchown(self.alias, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.lchown(self.alias, 0, 0)
        with tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-link-', dir='/root') as external:
            os.link(self.alias, Path(external) / 'alias-hardlink', follow_symlinks=False)
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'prepared alias inode'):
                self.hold(stack)

    def test_directory_owner_and_same_tree_parent_namespace_drift_refuse(self):
        path = self.root / 'opt/droiddeck/guest'
        os.chown(path, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(path, 0, 0)
        with ExitStack() as stack:
            held = self.hold(stack)
            self.parent.chmod(0o755)
            with self.assertRaises(ValueError):
                held['check']()
            self.parent.chmod(0o700)

    def test_alias_replacement_during_readlink_refuses_even_same_target_text(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            readlink = T.os.readlink
            def replaced(name, **kwargs):
                target = readlink(name, **kwargs)
                os.unlink(name, dir_fd=kwargs['dir_fd'])
                os.symlink(target, name, dir_fd=kwargs['dir_fd'])
                return target
            with mock.patch.object(T.os, 'readlink', side_effect=replaced), self.assertRaisesRegex(ValueError, 'prepared alias inode'):
                held['read_link'](next(iter(self.tree['links'])))

    def test_content_and_repaired_manifest_cannot_change_original_authority(self):
        self.file.write_bytes(b'changed')
        manifest = json.loads((self.root / T.A.MANIFEST).read_bytes())
        row = next(row for row in manifest['files'] if row['path'] == 'etc/droiddeck/runtime.json')
        row['size'], row['sha256'] = 7, T.E._sha(b'changed')
        (self.root / T.A.MANIFEST).write_bytes(T.E._encode(manifest, compact=True))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_later_same_byte_regular_and_alias_inode_replacements_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            before = self.file.read_bytes()
            self.file.unlink()
            self.file.write_bytes(before)
            self.file.chmod(0o644)
            with self.assertRaises(ValueError):
                held['check']()
        with ExitStack() as stack:
            held = self.hold(stack)
            target = os.readlink(self.alias)
            self.alias.unlink()
            self.alias.symlink_to(target)
            with self.assertRaises(ValueError):
                held['read_link'](next(iter(self.tree['links'])))

    def test_later_directory_replacement_and_new_inventory_refuse(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            path = self.root / 'opt/droiddeck/runtime'
            path.rename(path.with_name('runtime-saved'))
            path.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                held['check']()

    def test_later_file_directory_and_alias_target_mutations_refuse(self):
        for path, changed, restored in ((self.file, 0o600, 0o644), (self.root / 'opt/droiddeck/guest', 0o755, 0o700)):
            with ExitStack() as stack:
                held = self.hold(stack)
                path.chmod(changed)
                with self.assertRaises(ValueError):
                    held['check']()
            path.chmod(restored)
        with ExitStack() as stack:
            held = self.hold(stack)
            target = self.alias.parent / os.readlink(self.alias)
            target.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                held['check']()

    def test_original_generation_or_identity_drift_refuses_even_unchanged_prepared_tree(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.upstream['read_plan'].return_value = b'changed'
            with self.assertRaisesRegex(ValueError, 'Original final placement authority changed'):
                held['read']('final-layout.json')

    def test_original_drift_during_prepared_read_is_caught_after_tree_check(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            read = T.A._read
            def changed(*args):
                data = read(*args)
                self.upstream['check'].side_effect = ValueError('generated identity drift')
                return data
            with mock.patch.object(T.A, '_read', side_effect=changed), self.assertRaisesRegex(ValueError, 'identity drift'):
                held['read']('final-layout.json')

    def test_closed_regular_and_alias_read_names(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            for name in (None, '../outside', '/etc/passwd', next(iter(self.tree['links']))):
                with self.assertRaises(ValueError):
                    held['read'](name)
            for name in (None, 'etc/droiddeck/runtime.json', '../outside'):
                with self.assertRaises(ValueError):
                    held['read_link'](name)

    def test_budget_refusal_opens_no_tree_descriptors(self):
        import resource
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(1, 1)):
            with mock.patch.object(T.os, 'open', side_effect=AssertionError('budget before opens')):
                with self.assertRaises(ValueError):
                    self.hold(stack)

    def test_failure_paths_close_regular_and_alias_descriptors(self):
        before = set(os.listdir('/proc/self/fd'))
        os.lchown(self.alias, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
