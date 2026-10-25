# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-prepared-stage.py')
spec = importlib.util.spec_from_file_location('prepared_stage_tests', FILE)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def fixture():
    runtime = {'assembly/runtime/generated-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': name, 'path': name.removeprefix('assembly/'), 'size': len(data),
             'sha256': R.E._sha(data), 'mode': 0o644} for name, data in runtime.items()]
    for name, (path, mode) in R.K.L.ARTIFACT_LAYOUT.items():
        source, data = 'artifacts/' + name, ('generated ' + name).encode()
        runtime[source] = data
        rows.append({'source': source, 'path': path, 'size': len(data), 'sha256': R.E._sha(data), 'mode': mode})
    recipe = {name: ('generated ' + name).encode() for name in R.E.RECIPE_FILES}
    raw = R.E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': R.E._records(recipe, recipe),
        'links': [{'path': name, 'target': target} for name, target in sorted(R.K.L.LINKS.items())]}, compact=True)
    layout, readers = R.K._derive(raw)
    upstream = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=raw),
        'read_source': mock.Mock(side_effect=runtime.__getitem__), 'read_recipe_file': mock.Mock(side_effect=recipe.__getitem__), 'policy': {}}
    with ExitStack() as stack, mock.patch.object(R.K.E, 'hold_owned_install_plan', return_value=upstream):
        held = R.K.hold_final_install_layout(stack, 'u', '/root/source', '/root/recipes', b'{}')
    held = {**held, **{name: mock.Mock(wraps=held[name]) for name in ('check', 'read_plan', 'read_file')}}
    payload = {name[1:]: (runtime if kind == 'runtime' else recipe)[source]
               for name, (kind, source, _, _) in readers.items()}
    tree = R.Q._spec(layout)
    payload.update({'final-layout.json': layout, R.A.MANIFEST: tree['manifest']})
    return held, upstream, tree, payload


class Boundary(unittest.TestCase):
    def test_both_tree_budget_counts_created_and_observed_alias_fds(self):
        _, _, tree, _ = fixture()
        resource = SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(10000, 10000)))
        parent = PurePosixPath('/root/generated')
        with mock.patch.dict(sys.modules, resource=resource), mock.patch.object(R.os, 'listdir', return_value=['0', '1', '2']):
            expected = 3 + 2 * (len((parent / 'prepared').parts) + len(tree['directories']) - 1 + 261 + 4) + 16
            self.assertEqual(R._budget(parent, tree)['preparedPublicationDescriptorUpperBound'], expected)
            resource.getrlimit.return_value = (expected - 1, expected - 1)
            with self.assertRaises(ValueError):
                R._budget(parent, tree)

    def test_root_paths_bounds_and_closed_redacted_cli(self):
        with ExitStack() as stack, mock.patch.object(R.M, '_preflight_parent', side_effect=AssertionError('no filesystem')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(R.A.sys, 'platform', platform), mock.patch.object(R.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        R.publish_prepared_install_tree(stack, 'u', '/root/s', '/root/r', '/root/p', b'{}')
            with mock.patch.object(R.A, '_root_linux'):
                for parent in ('/root', '/opt/droiddeck', '/etc/droiddeck', '/root/s/sub', '/root/r/sub', '/root//bad'):
                    with self.assertRaises(ValueError):
                        R.publish_prepared_install_tree(stack, 'u', '/root/s', '/root/r', parent, b'{}')
                with self.assertRaises(ValueError):
                    R.publish_prepared_install_tree(stack, 'u', '/root/s', '/root/r', '/root/p', b'x' * 16385)
        for args in ([], ['--install'], ['--recover'], ['--stage-prepared-tree', 'u', '/s', '/r', '/p', '-', '--mode', '755']):
            with self.assertRaises(ValueError):
                R.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Prepared tree staging refused; no installation or activation performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated prepared-publication fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-prepared-stage-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'stage'
        self.parent.mkdir(mode=0o700)
        self.original, self.upstream, self.tree, self.payload = fixture()
        self.addCleanup(mock.patch.stopall)
        self.admit = mock.patch.object(R.K, 'hold_final_install_layout', return_value=self.original).start()

    def publish(self, stack):
        return R.publish_prepared_install_tree(stack, 'u', '/root/source', '/root/recipes', str(self.parent), b'{}')

    def test_complete_original_bound_261_files_four_aliases_modes_ownership_and_fd_closure(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        parent_identity = R.Q.S._directory(self.parent.stat(), True)
        changed, writes = [], []
        chmod, write, observe = R.os.fchmod, R.S._write_private, R.Q._hold_tree
        def changed_mode(fd, mode):
            changed.append(os.readlink('/proc/self/fd/' + str(fd)))
            chmod(fd, mode)
        def written(*args):
            writes.append(args[2])
            attempt = next(self.parent.glob('.prepared-attempt-*'))
            destination = Path(os.readlink('/proc/self/fd/' + str(args[1]))) / args[2]
            if destination == attempt / R.A.MANIFEST:
                for name, target in self.tree['links'].items():
                    self.assertEqual(os.readlink(attempt / name), target)
                for name, mode in self.tree['directories'].items():
                    self.assertEqual(stat.S_IMODE((attempt / name).stat().st_mode), mode)
            return write(*args)
        with ExitStack() as stack, mock.patch.object(R.os, 'fchmod', side_effect=changed_mode), mock.patch.object(R.S, '_write_private', side_effect=written):
            with mock.patch.object(R.Q, '_hold_tree', wraps=observe) as observer:
                with mock.patch.object(R.M.subprocess, 'run', side_effect=AssertionError('no copied artifact/service execution')):
                    held = self.publish(stack)
            observer.assert_called_once()
            self.admit.assert_called_once()
            self.original['read_plan'].assert_called_once_with()
            self.assertEqual(self.original['read_file'].call_count, 259)
            self.assertEqual((len(held['files']), len(held['links'])), (261, 4))
            root = self.parent / 'prepared'
            for name, data in self.payload.items():
                self.assertEqual(held['read'](name), data)
                info = (root / name).lstat()
                self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)), (0, 0, 1, self.tree['files'][name][2]))
            for name, target in self.tree['links'].items():
                self.assertEqual(held['read_link'](name), target)
            self.assertIs(held['policy']['createdInodeOwnershipVerified'], True)
            self.assertEqual(held['policy']['createdInodeOwnershipScope'], 'new-private-prepared-tree-only')
            for flag in ('finalPermissionsApplied', 'runtimeLinksMaterialized', 'finalTargetsPublished', 'servicesRegistered',
                         'servicesActivated', 'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation'):
                self.assertIs(held['policy'][flag], False)
            self.assertTrue(all(name.startswith(str(self.parent / '.prepared-attempt-')) for name in changed))
            self.assertTrue(changed)
            self.assertEqual(writes[-1], R.A.MANIFEST)
            self.assertFalse(list(self.parent.glob('.prepared-attempt-*')))
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), held['policy']['preparedPublicationDescriptorUpperBound'])
        self.assertEqual(R.Q.S._directory(self.parent.stat(), True), parent_identity)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_existing_entries_and_nonprivate_parent_refuse_before_source_admission(self):
        path = self.parent / 'foreign'
        for kind in ('file', 'directory', 'symlink'):
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
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.admit.assert_not_called()

    def test_insufficient_descriptor_budget_and_source_bytes_leave_parent_empty(self):
        with ExitStack() as stack, mock.patch.object(R, '_budget', side_effect=ValueError('generated')):
            with self.assertRaises(ValueError):
                self.publish(stack)
        self.original['read_file'].assert_not_called()
        self.original['read_file'].side_effect = lambda _: b'changed'
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_source_generation_drift_during_prefetch_refuses_before_attempt(self):
        read = self.original['read_file']._mock_wraps
        def changed(name):
            data = read(name)
            self.upstream['read_plan'].return_value = b'changed'
            return data
        self.original['read_file'].side_effect = changed
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_parent_changed_during_prefetch_is_independently_readmitted(self):
        read = self.original['read_file']._mock_wraps
        def changed(name):
            (self.parent / 'foreign').touch(exist_ok=True)
            return read(name)
        self.original['read_file'].side_effect = changed
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual([p.name for p in self.parent.iterdir()], ['foreign'])

    def test_partial_write_preserves_attempt_and_no_manifest(self):
        write = R.S._write_private
        def failed(*args):
            write(*args)
            raise OSError('generated failure')
        with ExitStack() as stack, mock.patch.object(R.S, '_write_private', side_effect=failed), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        attempt = next(self.parent.glob('.prepared-attempt-*'))
        self.assertFalse((attempt / R.A.MANIFEST).exists())
        self.assertFalse((self.parent / 'prepared').exists())

    def test_exclusive_file_collision_preserves_foreign_bytes(self):
        write = R.S._write_private
        def raced(stack, directory, name, raw):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            try:
                os.write(fd, b'foreign generated')
            finally:
                os.close(fd)
            return write(stack, directory, name, raw)
        with ExitStack() as stack, mock.patch.object(R.S, '_write_private', side_effect=raced), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        attempt = next(self.parent.glob('.prepared-attempt-*'))
        regulars = [p for p in attempt.rglob('*') if p.is_file()]
        self.assertEqual([p.read_bytes() for p in regulars], [b'foreign generated'])

    def test_fchmod_namespace_substitution_cannot_be_adopted(self):
        chmod = R.os.fchmod
        def raced(fd, mode):
            chmod(fd, mode)
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode):
                path = Path(os.readlink('/proc/self/fd/' + str(fd)))
                data = path.read_bytes()
                path.unlink()
                path.write_bytes(data)
                path.chmod(mode)
        with ExitStack() as stack, mock.patch.object(R.os, 'fchmod', side_effect=raced), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue(list(self.parent.glob('.prepared-attempt-*')))
        self.assertFalse((self.parent / 'prepared').exists())

    def test_mode_transition_failure_preserves_attempt_without_changing_parent(self):
        before = R.S._directory(self.parent.stat(), True)
        with ExitStack() as stack, mock.patch.object(R.os, 'fchmod', side_effect=OSError('generated')), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual(R.S._directory(self.parent.stat(), True), before)
        self.assertTrue(list(self.parent.glob('.prepared-attempt-*')))

    def test_directory_namespace_substitution_during_mode_transition_refuses(self):
        chmod, changed = R.os.fchmod, False
        def raced(fd, mode):
            nonlocal changed
            chmod(fd, mode)
            if stat.S_ISDIR(os.fstat(fd).st_mode) and not changed:
                changed = True
                path = Path(os.readlink('/proc/self/fd/' + str(fd)))
                path.rmdir()
                path.mkdir(mode=mode)
        with ExitStack() as stack, mock.patch.object(R.os, 'fchmod', side_effect=raced), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue(changed)
        self.assertFalse((self.parent / 'prepared').exists())

    def test_source_drift_during_copy_preserves_attempt(self):
        write = R.S._write_private
        def changed(*args):
            result = write(*args)
            self.upstream['read_plan'].return_value = b'changed'
            return result
        with ExitStack() as stack, mock.patch.object(R.S, '_write_private', side_effect=changed), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue(list(self.parent.glob('.prepared-attempt-*')))

    def test_alias_failure_preserves_attempt_before_manifest(self):
        with ExitStack() as stack, mock.patch.object(R.os, 'symlink', side_effect=OSError('generated')), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        attempt = next(self.parent.glob('.prepared-attempt-*'))
        self.assertFalse((attempt / R.A.MANIFEST).exists())

    def test_alias_substitution_after_open_refuses_same_target_inode_swap(self):
        opened, changed = R.os.open, False
        def raced(name, flags, *args, **kwargs):
            nonlocal changed
            fd = opened(name, flags, *args, **kwargs)
            if flags & os.O_PATH and not changed:
                changed = True
                target = os.readlink(name, dir_fd=kwargs['dir_fd'])
                os.unlink(name, dir_fd=kwargs['dir_fd'])
                os.symlink(target, name, dir_fd=kwargs['dir_fd'])
            return fd
        with ExitStack() as stack, mock.patch.object(R.os, 'open', side_effect=raced), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue(changed)
        self.assertTrue(list(self.parent.glob('.prepared-attempt-*')))

    def test_no_replace_rename_collision_preserves_foreign_prepared_and_attempt(self):
        rename = R._rename_new
        def raced(fd, attempt):
            (self.parent / 'prepared').mkdir(mode=0o700)
            (self.parent / 'prepared/foreign').write_bytes(b'foreign generated')
            rename(fd, attempt)
        with ExitStack() as stack, mock.patch.object(R, '_rename_new', side_effect=raced), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual((self.parent / 'prepared/foreign').read_bytes(), b'foreign generated')
        self.assertTrue(list(self.parent.glob('.prepared-attempt-*')))

    def test_postrename_fsync_failure_preserves_published_created_root(self):
        fsync = R.os.fsync
        def failed(fd):
            if (self.parent / 'prepared').exists():
                raise OSError('generated postrename failure')
            fsync(fd)
        with ExitStack() as stack, mock.patch.object(R.os, 'fsync', side_effect=failed), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'prepared').is_dir())
        self.assertFalse(list(self.parent.glob('.prepared-attempt-*')))

    def test_independent_verifier_failure_preserves_published_tree(self):
        with ExitStack() as stack, mock.patch.object(R.Q, '_hold_tree', side_effect=ValueError('generated')) as observer:
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                self.publish(stack)
            observer.assert_called_once()
        self.assertTrue((self.parent / 'prepared').is_dir())
        self.assertFalse(list(self.parent.glob('.prepared-attempt-*')))

    def test_source_generation_drift_after_rename_preserves_published_tree(self):
        rename = R._rename_new
        def changed(fd, attempt):
            rename(fd, attempt)
            self.upstream['read_plan'].return_value = b'changed'
        with ExitStack() as stack, mock.patch.object(R, '_rename_new', side_effect=changed), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'prepared').is_dir())

    def test_later_same_byte_regular_inode_and_alias_substitution_are_uncertain(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            path = self.parent / 'prepared/etc/droiddeck/runtime.json'
            data = path.read_bytes()
            path.unlink()
            path.write_bytes(data)
            path.chmod(0o644)
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                held['check']()
        with tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-prepared-stage-', dir='/root') as temp:
            self.parent = Path(temp)
            with ExitStack() as stack:
                held = self.publish(stack)
                name, target = next(iter(self.tree['links'].items()))
                path = self.parent / 'prepared' / name
                path.unlink()
                path.symlink_to(target)
                with self.assertRaises(R.S.CandidatePublicationUncertain):
                    held['read_link'](name)

    def test_later_mode_hardlink_source_and_closed_read_refusals_are_uncertain(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            for name in (None, '../foreign', next(iter(self.tree['links']))):
                with self.assertRaises(R.S.CandidatePublicationUncertain):
                    held['read'](name)
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                held['read_link']('final-layout.json')
            self.upstream['check'].side_effect = ValueError('generated identity drift')
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                held['read']('final-layout.json')
            self.upstream['check'].side_effect = None
            path = self.parent / 'prepared/etc/droiddeck/runtime.json'
            path.chmod(0o600)
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                held['check']()
            path.chmod(0o644)
            os.link(path, self.parent / 'foreign-hardlink')
            with self.assertRaises(R.S.CandidatePublicationUncertain):
                held['check']()

    def test_interrupted_copy_and_published_failure_close_all_descriptors(self):
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack, mock.patch.object(R.Q, '_hold_tree', side_effect=ValueError('generated')), self.assertRaises(R.S.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_later_created_file_hardlink_refuses_with_unchanged_tree_inventory(self):
        with tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-prepared-stage-', dir='/root') as external:
            with ExitStack() as stack:
                held = self.publish(stack)
                path = self.parent / 'prepared/etc/droiddeck/runtime.json'
                os.link(path, Path(external) / 'hardlink')
                with self.assertRaises(R.S.CandidatePublicationUncertain):
                    held['check']()


if __name__ == '__main__':
    unittest.main()
