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

FILE = Path(__file__).with_name('runtime-publication-journal.py')
spec = importlib.util.spec_from_file_location('publication_journal_test', FILE)
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)


def prefix():
    result = []
    for index, phase in enumerate(J.PHASES):
        result.append(J._encode({'version': 1, 'kind': 'private-publication-progress',
            'transaction': 'a' * 32, 'planSha256': 'b' * 64, 'step': index, 'phase': phase,
            'previousSha256': J._sha(result[-1]) if result else '0' * 64}))
    return tuple(result)


class Boundary(unittest.TestCase):
    def test_every_prefix_requires_manual_review_even_verified(self):
        for count in range(1, len(J.PHASES) + 1):
            report = J._parse(prefix()[:count])
            self.assertEqual((report['records'], report['lastPhase']), (count, J.PHASES[count - 1]))
            self.assertEqual(report['review'], 'manual-review-required')
            for flag in (*J.FALSE_FLAGS, 'journalAuthenticityVerified', 'journalSuffixCompletenessVerified', 'livePublicationPerformed'):
                self.assertIs(report[flag], False)

    def test_closed_schema_canonical_bytes_and_bounded_input(self):
        original = prefix()[0]
        for raw in (b'', b'x' * (J.LIMIT + 1), b'{', original[:-1], original + b' ',
                    original.replace(b'"version":1', b'"version":true'),
                    original.replace(b'"step":0', b'"step":false'),
                    original.replace(b'"version":1', b'"extra":1,"version":1'),
                    original.replace(b'"version":1', b'"version":1,"version":1')):
            with self.assertRaises((ValueError, UnicodeError)):
                J._parse((raw,))
        for records in ((), [], (None,), prefix() + (original,)):
            with self.assertRaises(ValueError):
                J._parse(records)

    def test_reorder_gap_chain_plan_and_transaction_changes_refused(self):
        records = prefix()
        for bad in ((records[1],), (records[0], records[2]), tuple(reversed(records)),
                    (records[0], records[1].replace(b'a' * 32, b'c' * 32)),
                    (records[0], records[1].replace(b'b' * 64, b'c' * 64)),
                    (records[0], records[1].replace(J._sha(records[0]).encode(), b'0' * 64))):
            with self.assertRaises(ValueError):
                J._parse(bad)

    def test_entire_suffix_loss_not_detectable_and_never_authorizes_recovery(self):
        report = J._parse(prefix()[:2])
        self.assertFalse(report['journalSuffixCompletenessVerified'])
        self.assertFalse(report['recoveryAuthorized'])
        self.assertEqual(report['lastPhase'], 'runtime-publish-intent')

    def test_no_live_or_arbitrary_names_or_mutating_cli(self):
        for path in ('/root', '/opt/droiddeck', '/etc/droiddeck', '/var/lib/droiddeck-install-intent',
                     '/root/droiddeck-publication-journal-x/child', '/root/droiddeck-publication-journal-x/',
                     '/root/./droiddeck-publication-journal-x', '/root/droiddeck-publication-journal-..', None):
            with self.assertRaises(ValueError):
                J._path(path)
        for args in ([], ['--install'], ['--recover'], ['--create-private-journal', 'private-value'],
                     ['--inspect-private-journal', '/root', '--resume']):
            with self.assertRaises(ValueError):
                J.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Journal inspection refused; preserve state; no installation or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated journal fixtures required')
class Journal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-publication-journal-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.directory = self.parent / 'journal'
        self.addCleanup(mock.patch.stopall)

    def create(self, stack):
        return J.create_private_journal(stack, str(self.parent), 'b' * 64)

    def inspect(self, stack):
        return J.inspect_private_journal(stack, str(self.parent))

    def test_durable_all_phases_original_fds_modes_closure_no_limit_raise(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        sync, events = J.os.fsync, []
        def fsync(fd):
            events.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            sync(fd)
        with ExitStack() as stack, mock.patch.object(J.os, 'fsync', side_effect=fsync):
            writer = self.create(stack)
            for phase in J.PHASES[1:]:
                report = writer['advance'](phase)
                self.assertEqual(report['lastPhase'], phase)
            self.assertEqual(writer['observe']()['records'], 9)
            self.assertEqual(events, ['directory', 'directory'] + ['file', 'directory'] * 9)
            with self.assertRaises(ValueError):
                writer['advance']('verified')
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)
        self.assertEqual({p.name for p in self.parent.iterdir()}, {'journal'})
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in self.directory.iterdir():
            info = path.lstat()
            self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)), (0, 0, 1, 0o600))
        with ExitStack() as stack:
            self.assertEqual(self.inspect(stack)['observe']()['lastPhase'], 'verified')

    def test_order_refusal_before_write_writer_still_usable(self):
        class ForeignPhase(str):
            def __eq__(self, other):
                return True
        with ExitStack() as stack:
            writer = self.create(stack)
            for phase in ('registered', 'prepared', '--activate', None, ForeignPhase('generated foreign payload')):
                with self.assertRaises(ValueError):
                    writer['advance'](phase)
            self.assertEqual(writer['advance']('runtime-publish-intent')['records'], 2)

    def test_create_never_adopts_existing_directory_file_or_dangling_link(self):
        for kind in ('directory', 'file', 'symlink'):
            with self.subTest(kind=kind):
                path = self.parent / 'foreign'
                if kind == 'directory':
                    path.mkdir()
                elif kind == 'file':
                    path.write_bytes(b'generated foreign')
                else:
                    path.symlink_to('missing')
                with ExitStack() as stack, self.assertRaises(ValueError):
                    self.create(stack)
                self.assertFalse(self.directory.exists())
                if kind == 'directory':
                    path.rmdir()
                else:
                    path.unlink()

    def test_plan_payload_and_nonroot_refused_before_access(self):
        with ExitStack() as stack:
            for digest in (None, b'b' * 64, 'B' * 64, 'b' * 63, '{"private":true}'):
                with self.assertRaises(ValueError):
                    J.create_private_journal(stack, str(self.parent), digest)
            with mock.patch.object(J.os, 'geteuid', return_value=1000), self.assertRaises(ValueError):
                self.create(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_low_fd_budget_refuses_before_mutation(self):
        import resource
        with ExitStack() as stack, mock.patch.object(resource, 'getrlimit', return_value=(4, 4)), self.assertRaises(ValueError):
            self.create(stack)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_parent_symlink_and_wrong_mode_refused(self):
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.create(stack)
        self.parent.chmod(0o700)
        real = self.parent.with_name(self.parent.name + '-moved')
        self.parent.rename(real)
        self.parent.symlink_to(real, target_is_directory=True)
        try:
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.create(stack)
        finally:
            self.parent.unlink()
            real.rename(self.parent)

    def test_partial_write_preserved_and_poisoned_writer_never_retries(self):
        write = J.os.write
        with ExitStack() as stack:
            writer = self.create(stack)
            calls = []
            def broken(fd, data):
                calls.append(1)
                return write(fd, data[:17]) if len(calls) == 1 else 0
            with mock.patch.object(J.os, 'write', side_effect=broken), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            snapshot = {p.name: p.read_bytes() for p in self.directory.iterdir()}
            self.assertTrue(any(name.startswith('.record-') for name in snapshot))
            self.assertIn(b'', [raw[17:] for name, raw in snapshot.items() if name.startswith('.record-')])
            with self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            with self.assertRaises(J.JournalUncertain):
                writer['observe']()
            self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.directory.iterdir()})
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_rename_noreplace_collision_keeps_foreign_bytes(self):
        rename = J._rename_new
        with ExitStack() as stack:
            writer = self.create(stack)
            foreign = self.directory / '0001.json'
            def collide(fd, source, target):
                foreign.write_bytes(b'generated foreign')
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=collide), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            self.assertEqual(foreign.read_bytes(), b'generated foreign')
            self.assertTrue(any(p.name.startswith('.record-') for p in self.directory.iterdir()))

    def test_crash_after_rename_visible_prefix_is_manual_review_only(self):
        rename = J._rename_new
        with ExitStack() as stack:
            writer = self.create(stack)
            def interrupted(fd, source, target):
                rename(fd, source, target)
                raise OSError('generated crash after rename')
            with mock.patch.object(J, '_rename_new', side_effect=interrupted), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            with self.assertRaises(J.JournalUncertain):
                writer['observe']()
        with ExitStack() as stack:
            report = self.inspect(stack)['observe']()
            self.assertEqual((report['records'], report['review']), (2, 'manual-review-required'))
            self.assertFalse(report['recoveryAuthorized'])

    def test_directory_sync_failure_poisoned_and_no_cleanup(self):
        sync = J.os.fsync
        with ExitStack() as stack:
            writer = self.create(stack)
            def fail_directory(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('generated directory fsync failure')
                sync(fd)
            with mock.patch.object(J.os, 'fsync', side_effect=fail_directory), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            self.assertTrue((self.directory / '0001.json').exists())
            with self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')

    def test_file_sync_failure_preserves_unpublished_attempt(self):
        with ExitStack() as stack:
            writer = self.create(stack)
            with mock.patch.object(J.os, 'fsync', side_effect=OSError('generated file sync failure')), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            self.assertFalse((self.directory / '0001.json').exists())
            self.assertTrue(any(p.name.startswith('.record-') for p in self.directory.iterdir()))
            with self.assertRaises(J.JournalUncertain):
                writer['observe']()

    def test_short_writes_complete_before_record_publication(self):
        write = J.os.write
        with ExitStack() as stack:
            writer = self.create(stack)
            with mock.patch.object(J.os, 'write', side_effect=lambda fd, data: write(fd, data[:7])) as short:
                self.assertEqual(writer['advance']('runtime-publish-intent')['records'], 2)
                self.assertGreater(short.call_count, 10)
        with ExitStack() as stack:
            self.assertEqual(self.inspect(stack)['observe']()['records'], 2)

    def test_replacement_during_rename_refused_and_preserved(self):
        rename = J._rename_new
        with ExitStack() as stack:
            writer = self.create(stack)
            def replace(fd, source, target):
                path = self.directory / source
                data = path.read_bytes()
                path.unlink()
                path.write_bytes(data)
                path.chmod(0o600)
                rename(fd, source, target)
            with mock.patch.object(J, '_rename_new', side_effect=replace), self.assertRaises(J.JournalUncertain):
                writer['advance']('runtime-publish-intent')
            self.assertTrue((self.directory / '0001.json').exists())

    def test_inspection_refusal_closes_all_descriptors(self):
        with ExitStack() as stack:
            self.create(stack)
        initial = set(os.listdir('/proc/self/fd'))
        (self.directory / '0000.json').write_bytes(b'generated invalid record')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_abrupt_process_exit_after_rename_never_becomes_resume_permission(self):
        code = '''
import importlib.util, os, sys
from contextlib import ExitStack
spec = importlib.util.spec_from_file_location('child_journal', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with ExitStack() as stack:
    writer = module.create_private_journal(stack, sys.argv[2], 'b' * 64)
    rename = module._rename_new
    def interrupted(fd, source, target):
        rename(fd, source, target)
        os._exit(73)
    module._rename_new = interrupted
    writer['advance']('runtime-publish-intent')
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(FILE), str(self.parent)],
            capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (73, b'', b''))
        with ExitStack() as stack:
            report = self.inspect(stack)['observe']()
            self.assertEqual((report['records'], report['lastPhase']), (2, 'runtime-publish-intent'))
            self.assertFalse(report['recoveryAuthorized'])
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.create(stack)

    def test_committed_mutations_replaced_inode_mode_hardlink_and_bytes_refused(self):
        with ExitStack() as stack:
            writer = self.create(stack)
        path = self.directory / '0000.json'
        original = path.read_bytes()
        for kind in ('bytes', 'mode', 'hardlink', 'replace', 'symlink'):
            with self.subTest(kind=kind), ExitStack() as stack:
                observer = self.inspect(stack)
                if kind == 'bytes':
                    path.write_bytes(original.replace(b'prepared', b'tampered'))
                elif kind == 'mode':
                    path.chmod(0o644)
                elif kind == 'hardlink':
                    os.link(path, self.parent / 'extra-link')
                else:
                    path.unlink()
                    if kind == 'replace':
                        path.write_bytes(original)
                        path.chmod(0o600)
                    else:
                        path.symlink_to('missing')
                with self.assertRaises((ValueError, OSError)):
                    observer['observe']()
                extra = self.parent / 'extra-link'
                if extra.exists():
                    extra.unlink()
                path.unlink()
                path.write_bytes(original)
                path.chmod(0o600)

    def test_writer_ancestor_replacement_poisoned(self):
        moved = self.parent.with_name(self.parent.name + '-moved')
        with ExitStack() as stack:
            writer = self.create(stack)
            self.parent.rename(moved)
            self.parent.mkdir(mode=0o700)
            try:
                with self.assertRaises(J.JournalUncertain):
                    writer['advance']('runtime-publish-intent')
                self.assertEqual(list(self.parent.iterdir()), [])
            finally:
                self.parent.rmdir()
                moved.rename(self.parent)

    def test_missing_gap_unknown_and_unfinished_names_refused_no_reads(self):
        with ExitStack() as stack:
            self.create(stack)
        for name in ('0002.json', 'unknown', '.record-generated'):
            path = self.directory / name
            path.write_bytes(b'generated')
            path.chmod(0o600)
            with ExitStack() as stack, mock.patch.object(J, '_read', side_effect=AssertionError('no content reads')), self.assertRaises(ValueError):
                self.inspect(stack)
            path.unlink()
        (self.directory / '0000.json').unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.inspect(stack)

    def test_failed_initial_creation_preserves_journal_and_no_recreate(self):
        with ExitStack() as stack, mock.patch.object(J.os, 'fsync', side_effect=OSError('generated')), self.assertRaises(J.JournalUncertain):
            self.create(stack)
        self.assertTrue(self.directory.is_dir())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.create(stack)


if __name__ == '__main__':
    unittest.main()
