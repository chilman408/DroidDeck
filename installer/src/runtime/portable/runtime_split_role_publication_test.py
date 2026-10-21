# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-split-role-publication.py')
spec = importlib.util.spec_from_file_location('split_role_test', FILE)
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)
J = X.J


class Boundary(unittest.TestCase):
    def test_closed_namespaces_and_no_cli(self):
        for role, valid in (('runtime', '/root/droiddeck-split-runtime-x'), ('config', '/run/droiddeck-split-config-x')):
            self.assertEqual(str(X._path(valid, role)), valid)
            for bad in (None, '/opt/droiddeck', '/etc/droiddeck', valid + '/', valid + '/child',
                        valid.replace('/droiddeck', '/./droiddeck'), valid.replace('-x', '-..'),
                        valid.replace('/root/', '/run/') if role == 'runtime' else valid.replace('/run/', '/root/')):
                with self.assertRaises(ValueError):
                    X._path(bad, role)
        for args in ([], ['--install'], ['--recover'], ['--publish']):
            with self.assertRaises(ValueError):
                X.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No split publication CLI; no installation or recovery performed')

    def test_distinct_devices_mandatory(self):
        self.assertEqual(X.ROLES, ('runtime', 'config'))
        X._different_devices((1, 2))
        with self.assertRaises(ValueError):
            X._different_devices((2, 2))


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated split-device fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temps = [tempfile.TemporaryDirectory(prefix='droiddeck-split-runtime-', dir='/root'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-split-config-', dir='/run'),
                      tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-split-', dir='/root')]
        for entry in self.temps:
            self.addCleanup(entry.cleanup)
        self.parents = dict(zip(X.ROLES, (Path(entry.name) for entry in self.temps[:2])))
        self.log = Path(self.temps[2].name)
        self.journal = self.log / 'journal'
        self.payload_fds = {}
        self.assertNotEqual(self.parents['runtime'].stat().st_dev, self.parents['config'].stat().st_dev,
                            'Existing /root and /run mounts must differ; never mount or silently skip')

    def populate(self, stack):
        def build(role, directory):
            data = ('generated split ' + role + '\n').encode('ascii')
            fd = os.open('generated.bin', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            stack.callback(os.close, fd)
            self.assertEqual(os.write(fd, data), len(data))
            identity = J._regular(os.fstat(fd))
            self.payload_fds[role] = fd
            def check():
                J._entries(directory, {'generated.bin'})
                for info in (os.fstat(fd), os.stat('generated.bin', dir_fd=directory, follow_symlinks=False)):
                    if J._regular(info) != identity or J._read(fd) != data:
                        raise ValueError('Generated retained split payload changed')
            return {'check': check, 'sync': lambda: os.fsync(fd)}
        return build

    def prepare(self, stack, build=None):
        return X.prepare_split_publication(stack, str(self.parents['runtime']), str(self.parents['config']),
            str(self.log), 'c' * 64, self.populate(stack) if build is None else build)

    def phase(self):
        records = sorted(self.journal.glob('[0-9][0-9][0-9][0-9].json'))
        return json.loads(records[-1].read_bytes())['phase']

    def stage(self, role):
        return next(path for path in self.parents[role].iterdir() if path.name.startswith('.' + role + '-attempt-'))

    def assert_empty(self):
        for path in (*self.parents.values(), self.log):
            self.assertEqual(list(path.iterdir()), [])

    def assert_poisoned(self, held):
        for method in held.values():
            with self.assertRaises(X.Uncertain):
                method()

    def test_real_two_device_moves_binding_inodes_flags_and_fd_closure(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.prepare(stack)
            self.assertTrue(held['observe']()['privateParentSerializationHeld'])
            raw = (self.parents['runtime'] / 'allocation.json').read_bytes()
            allocation = json.loads(raw)
            self.assertEqual(allocation['kind'], 'private-split-publication-allocation')
            self.assertEqual([row['role'] for row in allocation['parents']], list(X.ROLES))
            self.assertNotEqual(*[row['device'] for row in allocation['parents']])
            self.assertEqual(json.loads((self.journal / '0000.json').read_bytes())['planSha256'], J._sha(raw))
            self.assertFalse(held['observe']()['privateCrossDeviceRolePublicationVerified'])
            for index, row in enumerate(allocation['roles'], 1):
                role = row['role']
                report = held['publish_next']()
                info = (self.parents[role] / role).stat()
                self.assertEqual((info.st_dev, info.st_ino), (row['device'], row['inode']))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual(report['privateRolesPublished'], index)
                self.assertTrue(report['privateDistinctDevicesVerified'])
                self.assertTrue(report['privateTargetOwnershipVerified'])
                self.assertIs(report['privateCrossDeviceRolePublicationVerified'], index == 2)
                for flag in (*J.FALSE_FLAGS, 'crossFilesystemPublicationVerified', 'globallyAtomic',
                             'persistentConfigDestinationVerified', 'payloadAdmissionVerified',
                             'livePublicationPerformed', 'registrationAttempted', 'restartRecoveryVerified',
                             'powerLossRecoveryVerified'):
                    self.assertIs(report[flag], False)
                self.assertEqual(self.phase(), role + '-published')
            self.assertEqual({p.name for p in self.parents['runtime'].iterdir()}, {'runtime', 'allocation.json'})
            self.assertEqual({p.name for p in self.parents['config'].iterdir()}, {'config'})
            self.assertEqual((self.parents['runtime'] / 'allocation.json').read_bytes(), raw)
            with self.assertRaises(ValueError):
                held['publish_next']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_parent_locks_cover_preparation_and_both_moves_without_lock_files(self):
        import fcntl
        with ExitStack() as descriptors:
            probes = [os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (*self.parents.values(), self.log)]
            for fd in probes:
                descriptors.callback(os.close, fd)
            with ExitStack() as stack:
                held = self.prepare(stack)
                for step in range(3):
                    for fd in probes:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if step < 2:
                        held['publish_next']()
            for fd in probes:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual({p.name for p in self.parents['runtime'].iterdir()}, {'runtime', 'allocation.json'})
        self.assertEqual({p.name for p in self.parents['config'].iterdir()}, {'config'})
        self.assertEqual({p.name for p in self.log.iterdir()}, {'journal'})

    def test_busy_parent_rejects_before_builder_or_allocation(self):
        import fcntl
        for parent in (*self.parents.values(), self.log):
            with self.subTest(parent=parent), ExitStack() as stack:
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                build = mock.Mock()
                with self.assertRaisesRegex(ValueError, 'busy'):
                    self.prepare(stack, build)
                build.assert_not_called()
                self.assert_empty()

    def test_partial_lock_failure_releases_earlier_locks_immediately(self):
        import fcntl
        real, count = fcntl.flock, 0
        def fail_second(fd, operation):
            nonlocal count
            if operation & fcntl.LOCK_EX:
                count += 1
                if count == 2:
                    raise BlockingIOError('generated contention')
            return real(fd, operation)
        with ExitStack() as stack:
            with mock.patch.object(fcntl, 'flock', side_effect=fail_second), self.assertRaisesRegex(ValueError, 'busy'):
                self.prepare(stack)
            # The failed caller's ExitStack is still open: locks must already be released.
            for parent in (*self.parents.values(), self.log):
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    real(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    real(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        self.assert_empty()

    def test_unsupported_locking_fails_before_any_allocation(self):
        import errno
        import fcntl
        with ExitStack() as stack, mock.patch.object(fcntl, 'flock', side_effect=OSError(errno.EOPNOTSUPP, 'generated unsupported')):
            with self.assertRaises(OSError):
                self.prepare(stack)
        self.assert_empty()

    def test_duplicate_parent_descriptors_are_refused_before_locking(self):
        import fcntl
        with ExitStack() as stack:
            fd = os.open(self.log, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, fd)
            with mock.patch.object(fcntl, 'flock') as lock, self.assertRaisesRegex(ValueError, 'distinct'):
                X._serialize_parents(stack, (fd, fd, fd))
            lock.assert_not_called()
        self.assert_empty()

    def test_competing_process_exit_releases_locks_without_stale_files(self):
        import select
        program = ('import fcntl, os, sys\n'
                   'fds = [os.open(p, os.O_RDONLY | os.O_DIRECTORY) for p in sys.argv[1:]]\n'
                   'for fd in fds: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
                   'print("locked", flush=True)\n'
                   'sys.stdin.read(1)\n')
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', program,
            *map(str, (*self.parents.values(), self.log))], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'Generated lock child did not become ready')
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'busy'):
                self.prepare(stack)
            self.assert_empty()
            child.terminate()
            child.wait(timeout=5)
            with ExitStack() as stack:
                held = self.prepare(stack)
                self.assertTrue(held['observe']()['privateParentSerializationHeld'])
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_each_move_uses_own_device_intent_then_own_parent_sync(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            rename, sync, events = J._rename_new, os.fsync, []
            ids = {role: (path.stat().st_dev, path.stat().st_ino) for role, path in self.parents.items()}
            def moved(fd, source, target):
                if target in X.ROLES:
                    info = os.fstat(fd)
                    self.assertEqual((info.st_dev, info.st_ino), ids[target])
                    self.assertEqual(self.phase(), target + '-publish-intent')
                    events.append('rename-' + target)
                rename(fd, source, target)
            def synced(fd):
                info = os.fstat(fd)
                for role, identity in ids.items():
                    if (info.st_dev, info.st_ino) == identity:
                        events.append('sync-' + role + '-' + self.phase())
                sync(fd)
            with mock.patch.object(J, '_rename_new', side_effect=moved), mock.patch.object(os, 'fsync', side_effect=synced):
                held['publish_next']()
                held['publish_next']()
            self.assertEqual(events, ['rename-runtime', 'sync-runtime-runtime-publish-intent',
                                      'rename-config', 'sync-config-config-publish-intent'])

    def test_budget_and_device_refusal_before_allocation(self):
        import resource
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(4, 4)), self.assertRaises(ValueError):
            self.prepare(stack)
        self.assert_empty()
        with ExitStack() as stack, mock.patch.object(X, '_different_devices', side_effect=ValueError('generated same device')), self.assertRaises(ValueError):
            self.prepare(stack)
        self.assert_empty()

    def test_capacity_refusal_precedes_allocation_and_payload_builder(self):
        with ExitStack() as stack, mock.patch.object(X, '_storage_preflight', side_effect=ValueError('generated low capacity')) as storage:
            build = mock.Mock()
            with self.assertRaisesRegex(ValueError, 'low capacity'):
                self.prepare(stack, build)
            build.assert_not_called()
            self.assertEqual(set(storage.call_args.args[0]), {'runtime', 'config', 'journal'})
            self.assert_empty()

    def test_live_parent_capacity_observation_reports_no_reservation(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            report = held['observe']()
            self.assertTrue(report['privateParentSpacePreflightVerified'])
            self.assertFalse(report['privatePayloadSpacePlanSupplied'])
            self.assertFalse(report['storageCapacityReserved'])
            self.assertFalse(report['storageSuccessGuaranteed'])
            self.assertEqual(report['storageFilesystemsObserved'], len({p.stat().st_dev for p in (*self.parents.values(), self.log)}))

    def test_wrong_digest_or_builder_before_writes(self):
        for digest, builder in ((None, lambda: None), ('C' * 64, lambda: None), ('c' * 64, None)):
            with ExitStack() as stack, self.assertRaises(ValueError):
                X.prepare_split_publication(stack, str(self.parents['runtime']), str(self.parents['config']), str(self.log), digest, builder)
        self.assert_empty()

    def test_foreign_entries_in_either_parent_refused_without_other_writes(self):
        for path in (*self.parents.values(), self.log):
            foreign = path / 'foreign'
            foreign.write_bytes(b'generated foreign')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.prepare(stack)
            self.assertEqual(foreign.read_bytes(), b'generated foreign')
            foreign.unlink()
            self.assert_empty()

    def test_config_parent_symlink_refused_before_any_allocation(self):
        config = self.parents['config']
        config.rmdir()
        config.symlink_to(self.parents['runtime'])
        try:
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.prepare(stack)
            self.assertEqual(list(self.parents['runtime'].iterdir()), [])
            self.assertEqual(list(self.log.iterdir()), [])
        finally:
            config.unlink()
            config.mkdir(mode=0o700)

    def test_config_parent_mode_drift_refused(self):
        self.parents['config'].chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assert_empty()

    def test_second_role_collision_preserves_first_and_foreign_target(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            original = (self.parents['runtime'] / 'runtime').stat().st_ino
            rename = J._rename_new
            def collide(fd, source, target):
                if target == 'config':
                    (self.parents['config'] / 'config').symlink_to('missing')
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=collide), self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertEqual((self.parents['runtime'] / 'runtime').stat().st_ino, original)
            self.assertTrue((self.parents['config'] / 'config').is_symlink())
            self.assertTrue(self.stage('config').is_dir())
            self.assertEqual(self.phase(), 'config-publish-intent')
            self.assert_poisoned(held)

    def test_first_role_collision_never_replaces_foreign_file(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            rename = J._rename_new
            def collide(fd, source, target):
                if target == 'runtime':
                    (self.parents['runtime'] / target).write_bytes(b'foreign')
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=collide), self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertEqual((self.parents['runtime'] / 'runtime').read_bytes(), b'foreign')
            self.assertTrue(self.stage('runtime').is_dir())
            self.assertEqual(self.phase(), 'runtime-publish-intent')
            self.assert_poisoned(held)

    def test_config_parent_sync_failure_preserves_both_roles_but_only_intent(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            parent = self.parents['config'].stat()
            sync = os.fsync
            def fail(fd):
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                    raise OSError('generated config parent sync failure')
                sync(fd)
            with mock.patch.object(os, 'fsync', side_effect=fail), self.assertRaises(X.Uncertain):
                held['publish_next']()
            for role, path in self.parents.items():
                self.assertTrue((path / role).is_dir())
            self.assertEqual(self.phase(), 'config-publish-intent')
            self.assert_poisoned(held)

    def test_intent_sync_failure_prevents_first_move(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            sync = os.fsync
            def fail(fd):
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('generated journal intent sync failure')
                sync(fd)
            with mock.patch.object(os, 'fsync', side_effect=fail), self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertFalse((self.parents['runtime'] / 'runtime').exists())
            self.assertTrue(self.stage('runtime').is_dir())
            self.assert_poisoned(held)

    def test_second_completion_record_failure_preserves_both_roles(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            rename = J._rename_new
            def fail(fd, source, target):
                if target == '0004.json':
                    raise OSError('generated config completion failure')
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=fail), self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertEqual(self.phase(), 'config-publish-intent')
            for role, path in self.parents.items():
                self.assertTrue((path / role).is_dir())
            self.assertTrue(any(path.name.startswith('.record-') for path in self.journal.iterdir()))
            self.assert_poisoned(held)

    def test_payload_drift_before_move_poisoned(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            os.pwrite(self.payload_fds['config'], b'changed', 0)
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertEqual(self.phase(), 'prepared')
            self.assert_poisoned(held)

    def test_journal_drift_during_second_payload_sync_prevents_second_move(self):
        armed = [False]
        with ExitStack() as stack:
            build = self.populate(stack)
            def populate(role, fd):
                payload = build(role, fd)
                def sync():
                    payload['sync']()
                    if armed[0] and role == 'config':
                        (self.journal / '0000.json').chmod(0o644)
                return {'check': payload['check'], 'sync': sync}
            held = self.prepare(stack, populate)
            held['publish_next']()
            armed[0] = True
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertFalse((self.parents['config'] / 'config').exists())
            self.assertTrue((self.parents['runtime'] / 'runtime').exists())
            self.assert_poisoned(held)

    def test_parent_replacement_is_not_adopted(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            config = self.parents['config']
            preserved = config.with_name(config.name + '-preserved')
            config.rename(preserved)
            self.addCleanup(shutil.rmtree, preserved)
            config.mkdir(mode=0o700)
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assertTrue(any(p.name.startswith('.config-attempt-') for p in preserved.iterdir()))
            self.assert_poisoned(held)

    def test_allocation_same_byte_replacement_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            record = self.parents['runtime'] / 'allocation.json'
            raw = record.read_bytes()
            record.unlink()
            record.write_bytes(raw)
            record.chmod(0o600)
            with self.assertRaises(X.Uncertain):
                held['observe']()
            self.assert_poisoned(held)

    def test_allocation_hardlink_refused_even_outside_parent_inventory(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            os.link(self.parents['runtime'] / 'allocation.json', self.log / 'generated-hardlink')
            with self.assertRaises(X.Uncertain):
                held['observe']()
            self.assert_poisoned(held)

    def test_created_stage_replacement_is_not_adopted(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            stage = self.stage('config')
            stage.rename(stage.with_name('preserved-stage'))
            stage.mkdir(mode=0o700)
            with self.assertRaises(X.Uncertain):
                held['publish_next']()
            self.assert_poisoned(held)

    def test_builder_failure_preserves_allocations_closes_fds_and_no_journal(self):
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            build = self.populate(stack)
            def fail(role, fd):
                if role == 'config':
                    raise OSError('generated builder failure')
                return build(role, fd)
            with self.assertRaises(X.Uncertain):
                self.prepare(stack, fail)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertTrue((self.parents['runtime'] / 'allocation.json').is_file())
        for role in X.ROLES:
            self.assertTrue(self.stage(role).is_dir())
        self.assertEqual(list(self.log.iterdir()), [])

    def test_empty_builder_result_preserves_allocation(self):
        with ExitStack() as stack, self.assertRaises(X.Uncertain):
            self.prepare(stack, lambda role, fd: {'check': lambda: None, 'sync': lambda: None})
        self.assertTrue((self.parents['runtime'] / 'allocation.json').exists())
        self.assertEqual(list(self.log.iterdir()), [])

    def test_abrupt_child_exit_after_second_move_preserves_cross_device_evidence(self):
        code = '''
from contextlib import ExitStack
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('split_child', sys.argv[1])
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
fixture = tests.Publication('test_real_two_device_moves_binding_inodes_flags_and_fd_closure')
fixture.payload_fds = {}
with ExitStack() as stack:
    held = tests.X.prepare_split_publication(stack, *sys.argv[2:5], 'c' * 64, fixture.populate(stack))
    held['publish_next']()
    rename = tests.J._rename_new
    def crash(fd, source, target):
        rename(fd, source, target)
        if target == 'config':
            os._exit(82)
    tests.J._rename_new = crash
    held['publish_next']()
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(Path(__file__)),
            str(self.parents['runtime']), str(self.parents['config']), str(self.log)], capture_output=True, timeout=15)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (82, b'', b''))
        allocation = json.loads((self.parents['runtime'] / 'allocation.json').read_bytes())
        for row in allocation['roles']:
            info = (self.parents[row['role']] / row['role']).stat()
            self.assertEqual((info.st_dev, info.st_ino), (row['device'], row['inode']))
        self.assertEqual(self.phase(), 'config-publish-intent')
        with ExitStack() as stack:
            report = J.inspect_private_journal(stack, str(self.log))['observe']()
            self.assertEqual(report['lastPhase'], 'config-publish-intent')
            self.assertFalse(report['recoveryAuthorized'])
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)


if __name__ == '__main__':
    unittest.main()
