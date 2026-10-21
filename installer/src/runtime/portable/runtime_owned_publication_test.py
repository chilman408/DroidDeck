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

FILE = Path(__file__).with_name('runtime-owned-publication.py')
spec = importlib.util.spec_from_file_location('owned_publication_test', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
J = P.J


class Boundary(unittest.TestCase):
    def test_no_live_names_or_cli(self):
        for path in (None, '/opt/droiddeck', '/etc/droiddeck', '/root',
                     '/root/droiddeck-owned-publication-x/', '/root/droiddeck-owned-publication-x/child',
                     '/root/./droiddeck-owned-publication-x', '/root/droiddeck-owned-publication-..'):
            with self.assertRaises(ValueError):
                P._parent_path(path)
        for args in ([], ['--install'], ['--publish'], ['--recover'], ['--resume', 'private-value']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'],
            capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'No publication CLI; no installation or recovery performed')

    def test_exact_fixed_roles(self):
        self.assertEqual(P.ROLES, ('runtime', 'config'))


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated owned-publication fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-owned-publication-', dir='/root')
        self.log = tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.log.cleanup)
        self.parent, self.journal = Path(self.temp.name), Path(self.log.name) / 'journal'
        self.addCleanup(mock.patch.stopall)
        self.payload_fds, self.stage_fds = {}, {}

    def populate(self, stack):
        def build(role, directory):
            self.stage_fds[role] = directory
            data = ('generated ' + role + ' payload\n').encode()
            fd = os.open('generated.bin', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            stack.callback(os.close, fd)
            os.write(fd, data)
            identity = J._regular(os.fstat(fd))
            self.payload_fds[role] = fd
            def check():
                J._entries(directory, {'generated.bin'})
                for info in (os.fstat(fd), os.stat('generated.bin', dir_fd=directory, follow_symlinks=False)):
                    if J._regular(info) != identity or J._read(fd) != data:
                        raise ValueError('Generated retained payload changed')
            return {'check': check, 'sync': lambda: os.fsync(fd)}
        return build

    def prepare(self, stack, build=None):
        return P.prepare_private_publication(stack, str(self.parent), self.log.name, 'b' * 64,
            self.populate(stack) if build is None else build)

    def journal_phase(self):
        names = sorted(self.journal.glob('[0-9][0-9][0-9][0-9].json'))
        return json.loads(names[-1].read_bytes())['phase']

    def test_two_real_no_replace_moves_original_inodes_and_journal_binding(self):
        import resource
        initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            held = self.prepare(stack)
            record = (self.parent / 'allocation.json').read_bytes()
            allocation = json.loads(record)
            original = {row['role']: (row['device'], row['inode']) for row in allocation['roles']}
            self.assertEqual(allocation['scope'], 'private-current-session-only')
            first = json.loads((self.journal / '0000.json').read_bytes())
            self.assertEqual(first['planSha256'], J._sha(record))
            self.assertEqual(held['observe']()['privateRolesPublished'], 0)
            for index, role in enumerate(P.ROLES, 1):
                report = held['publish_next']()
                info = (self.parent / role).stat()
                self.assertEqual((info.st_dev, info.st_ino), original[role])
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual(report['privateRolesPublished'], index)
                self.assertTrue(report['privateTargetOwnershipVerified'])
                for flag in (*J.FALSE_FLAGS, 'payloadAdmissionVerified', 'livePublicationPerformed', 'registrationAttempted', 'restartRecoveryVerified'):
                    self.assertIs(report[flag], False)
                self.assertEqual(self.journal_phase(), role + '-published')
            self.assertEqual((self.parent / 'allocation.json').read_bytes(), record)
            self.assertEqual({p.name for p in self.parent.iterdir()}, {'runtime', 'config', 'allocation.json'})
            with self.assertRaises(ValueError):
                held['publish_next']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limits)

    def test_intent_is_synced_before_rename_completion_after_parent_sync(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            rename, sync, events = J._rename_new, P.os.fsync, []
            def moved(fd, source, target):
                if target in P.ROLES:
                    self.assertEqual(self.journal_phase(), target + '-publish-intent')
                    events.append('rename-' + target)
                return rename(fd, source, target)
            def synced(fd):
                info, parent = os.fstat(fd), self.parent.stat()
                if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                    events.append('parent-sync-' + self.journal_phase())
                sync(fd)
            with mock.patch.object(J, '_rename_new', side_effect=moved), mock.patch.object(P.os, 'fsync', side_effect=synced):
                held['publish_next']()
                held['publish_next']()
            self.assertEqual(events, ['rename-runtime', 'parent-sync-runtime-publish-intent',
                                      'rename-config', 'parent-sync-config-publish-intent'])

    def test_existing_parent_entry_refused_before_allocation_or_journal(self):
        for kind in ('file', 'directory', 'symlink'):
            path = self.parent / 'runtime'
            if kind == 'file':
                path.write_bytes(b'generated foreign')
            elif kind == 'directory':
                path.mkdir()
            else:
                path.symlink_to('missing')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.prepare(stack)
            self.assertEqual(list(Path(self.log.name).iterdir()), [])
            self.assertEqual([p.name for p in self.parent.iterdir()], ['runtime'])
            path.rmdir() if kind == 'directory' else path.unlink()

    def test_first_role_collision_preserves_foreign_destination_and_attempt(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            rename = J._rename_new
            def collide(fd, source, target):
                if target == 'runtime':
                    (self.parent / target).mkdir(mode=0o700)
                    (self.parent / target / 'foreign').write_bytes(b'generated foreign')
                return rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=collide), self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertEqual((self.parent / 'runtime' / 'foreign').read_bytes(), b'generated foreign')
            self.assertTrue(any(p.name.startswith('.runtime-attempt-') for p in self.parent.iterdir()))
            self.assertEqual(self.journal_phase(), 'runtime-publish-intent')
            with self.assertRaises(P.PublicationUncertain):
                held['publish_next']()

    def test_second_role_collision_preserves_first_published_inode(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            original = (self.parent / 'runtime').stat().st_ino
            rename = J._rename_new
            def collide(fd, source, target):
                if target == 'config':
                    (self.parent / target).symlink_to('missing')
                return rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=collide), self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertEqual((self.parent / 'runtime').stat().st_ino, original)
            self.assertTrue((self.parent / 'config').is_symlink())
            self.assertEqual(self.journal_phase(), 'config-publish-intent')
            self.assertTrue(any(p.name.startswith('.config-attempt-') for p in self.parent.iterdir()))

    def test_intent_sync_failure_prevents_role_move(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            sync = J.os.fsync
            def fail(fd):
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('generated intent sync failure')
                sync(fd)
            with mock.patch.object(J.os, 'fsync', side_effect=fail), self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertFalse((self.parent / 'runtime').exists())
            self.assertTrue(any(p.name.startswith('.runtime-attempt-') for p in self.parent.iterdir()))

    def test_parent_sync_failure_after_rename_preserves_target_not_completed_record(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            sync, identity = P.os.fsync, self.parent.stat()
            def fail(fd):
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
                    raise OSError('generated target parent sync failure')
                sync(fd)
            with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertTrue((self.parent / 'runtime').is_dir())
            self.assertEqual(self.journal_phase(), 'runtime-publish-intent')
            with self.assertRaises(P.PublicationUncertain):
                held['observe']()

    def test_payload_drift_after_preparation_refuses_before_intent(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            os.pwrite(self.payload_fds['runtime'], b'changed', 0)
            with self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertEqual(self.journal_phase(), 'prepared')
            self.assertFalse((self.parent / 'runtime').exists())

    def test_stage_replacement_same_contents_not_adopted(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            stage = next(p for p in self.parent.iterdir() if p.name.startswith('.runtime-attempt-'))
            moved = stage.with_name(stage.name + '-preserved')
            stage.rename(moved)
            stage.mkdir(mode=0o700)
            with self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertTrue(moved.is_dir())
            self.assertFalse((self.parent / 'runtime').exists())

    def test_allocation_same_byte_replacement_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            path = self.parent / 'allocation.json'
            raw = path.read_bytes()
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises(P.PublicationUncertain):
                held['observe']()

    def test_allocation_mode_change_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            (self.parent / 'allocation.json').chmod(0o644)
            with self.assertRaises(P.PublicationUncertain):
                held['observe']()

    def test_allocation_hardlink_refused(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            os.link(self.parent / 'allocation.json', self.parent / 'foreign-link')
            with self.assertRaises(P.PublicationUncertain):
                held['observe']()

    def test_existing_journal_refuses_before_new_allocation(self):
        (Path(self.log.name) / 'foreign').write_bytes(b'generated foreign')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_completion_record_failure_preserves_moved_runtime_and_intent(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            rename = J._rename_new
            def fail(fd, source, target):
                if target == '0002.json':
                    raise OSError('generated completion record rename failure')
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=fail), self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertTrue((self.parent / 'runtime').is_dir())
            self.assertEqual(self.journal_phase(), 'runtime-publish-intent')
            self.assertTrue(any(p.name.startswith('.record-') for p in self.journal.iterdir()))

    def test_trusted_builder_failure_preserves_partial_roles_and_record(self):
        with ExitStack() as stack:
            build = self.populate(stack)
            def fail(role, fd):
                if role == 'config':
                    raise OSError('generated builder failure')
                return build(role, fd)
            with self.assertRaises(P.PublicationUncertain):
                self.prepare(stack, fail)
            self.assertTrue((self.parent / 'allocation.json').exists())
            self.assertEqual(len(list(self.parent.iterdir())), 3)
            self.assertEqual(list(Path(self.log.name).iterdir()), [])

    def test_abrupt_process_exit_after_first_directory_move_preserves_original_evidence(self):
        code = '''
from contextlib import ExitStack
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('child_owned_test', sys.argv[1])
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
fixture = tests.Publication('test_two_real_no_replace_moves_original_inodes_and_journal_binding')
fixture.payload_fds, fixture.stage_fds = {}, {}
with ExitStack() as stack:
    held = tests.P.prepare_private_publication(stack, sys.argv[2], sys.argv[3], 'b' * 64, fixture.populate(stack))
    rename = tests.J._rename_new
    def crash(fd, source, target):
        rename(fd, source, target)
        if target == 'runtime':
            os._exit(81)
    tests.J._rename_new = crash
    held['publish_next']()
'''
        # The child exits without Python cleanup; only this generated fixture is inspected.
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(Path(__file__)),
            str(self.parent), self.log.name], capture_output=True, timeout=15)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (81, b'', b''))
        allocation = json.loads((self.parent / 'allocation.json').read_bytes())
        runtime = next(row for row in allocation['roles'] if row['role'] == 'runtime')
        self.assertEqual((self.parent / 'runtime').stat().st_ino, runtime['inode'])
        self.assertFalse((self.parent / 'config').exists())
        self.assertTrue(any(p.name.startswith('.config-attempt-') for p in self.parent.iterdir()))
        with ExitStack() as stack:
            report = J.inspect_private_journal(stack, self.log.name)['observe']()
            self.assertEqual(report['lastPhase'], 'runtime-publish-intent')
            self.assertFalse(report['recoveryAuthorized'])
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)

    def test_failed_preparation_closes_descriptors_without_cleaning_evidence(self):
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack, self.assertRaises(P.PublicationUncertain):
            self.prepare(stack, lambda role, fd: None)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertTrue((self.parent / 'allocation.json').exists())

    def test_empty_payload_and_builder_failure_preserve_allocations_without_journal(self):
        with ExitStack() as stack, self.assertRaises(P.PublicationUncertain):
            self.prepare(stack, lambda role, fd: {'check': lambda: None, 'sync': lambda: None})
        self.assertTrue((self.parent / 'allocation.json').exists())
        self.assertEqual(len(list(self.parent.iterdir())), 3)
        self.assertEqual(list(Path(self.log.name).iterdir()), [])

    def test_wrong_input_and_low_descriptor_budget_before_writes(self):
        import resource
        with ExitStack() as stack:
            with mock.patch.object(resource, 'getrlimit', return_value=(4, 4)), self.assertRaises(ValueError):
                self.prepare(stack)
            for pin in (None, b'b' * 64, 'B' * 64, 'short'):
                with self.assertRaises(ValueError):
                    P.prepare_private_publication(stack, str(self.parent), self.log.name, pin, lambda: None)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_same_byte_published_root_replacement_poisoned(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            held['publish_next']()
            original = self.parent / 'runtime'
            original.rename(self.parent / 'preserved-runtime')
            original.mkdir(mode=0o700)
            with self.assertRaises(P.PublicationUncertain):
                held['observe']()

    def test_journal_drift_poisoned_before_first_move(self):
        with ExitStack() as stack:
            held = self.prepare(stack)
            (self.journal / '0000.json').chmod(0o644)
            with self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertFalse((self.parent / 'runtime').exists())

    def test_journal_drift_during_payload_sync_prevents_rename(self):
        armed = [False]
        with ExitStack() as stack:
            build = self.populate(stack)
            def populate(role, fd):
                result = build(role, fd)
                sync = result['sync']
                def synced():
                    sync()
                    if armed[0] and role == 'runtime':
                        (self.journal / '0000.json').chmod(0o644)
                return {'check': result['check'], 'sync': synced}
            held = self.prepare(stack, populate)
            armed[0] = True
            with self.assertRaises(P.PublicationUncertain):
                held['publish_next']()
            self.assertFalse((self.parent / 'runtime').exists())


if __name__ == '__main__':
    unittest.main()
