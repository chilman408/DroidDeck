# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('swap_tested', Path(__file__).with_name('host-swap.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_installed_fixed_columns_and_metadata_only_queries(self):
        runner = mock.Mock(return_value=b'4294963200 /swap.img\n')
        self.assertEqual(D.inventory(runner), {'/swap.img': 4294963200})
        runner.assert_called_once_with(['/usr/sbin/swapon', '--show=SIZE,NAME', '--bytes', '--raw', '--noheadings'])
        self.assertEqual(D.SIZE, 4294967296)
        self.assertEqual(D.MINIMUM, 4294963200)
        self.assertEqual(D.HEADROOM, 12884901888)

    def test_empty_and_escaped_names_supported_but_malformed_inventory_refused(self):
        self.assertEqual(D.inventory(lambda _: b''), {})
        self.assertEqual(D.inventory(lambda _: b'1234 /swap\\x20file\n'), {'/swap\\x20file': 1234})
        for raw in (b'0 /swap\n', b'-1 /swap\n', b'1 relative\n', b'1 /a\n2 /a\n',
                    b'1 /unescaped space\n', b'1 /a extra\n', b'x' * 65537):
            with self.assertRaises(ValueError):
                D.inventory(lambda _: raw)

    def test_command_is_bounded_and_forwards_only_owned_descriptor(self):
        with mock.patch.object(D.subprocess, 'run', return_value=SimpleNamespace(stdout=b'metadata')) as run:
            self.assertEqual(D.run(['/usr/sbin/mkswap', '/proc/self/fd/9'], pass_fds=(9,)), b'metadata')
            self.assertEqual(run.call_args.kwargs['pass_fds'], (9,))
            self.assertEqual(run.call_args.kwargs['timeout'], 120)
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['env'], D.ENV)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux isolated swap metadata fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-swap-fixture-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ('etc', 'etc/systemd', 'etc/systemd/system', 'source', 'journal'):
            (self.root / relative).mkdir(mode=0o700)
        self.source = self.root / 'source'
        self.raw = Path(__file__).with_name(D.UNIT).read_bytes()
        (self.source / D.UNIT).write_bytes(self.raw)
        self.fd = os.open(self.root / 'journal', os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        for name, value in (('ROOT', self.root), ('SIZE', 65536), ('MINIMUM', 61440)):
            patch = mock.patch.object(D, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.swaps = b''
        self.mount = 'ext4'
        self.load = b'not-found\n'
        self.dropins = ''
        self.active = b'active\n'
        self.enabled = b'enabled\n'
        self.calls = []
        self.reserve = D.Reserve(self.source, self.fd, self.runner)

    def runner(self, args, **kwargs):
        self.calls.append((list(map(str, args)), kwargs))
        if args[0] == '/usr/sbin/swapon':
            return self.swaps
        if args[0] == '/usr/bin/findmnt':
            return json.dumps({'filesystems': [{'target': str(self.root), 'fstype': self.mount}]}).encode()
        if args[0] == '/usr/sbin/mkswap':
            fd = kwargs['pass_fds'][0]
            self.assertEqual(args, ['/usr/sbin/mkswap', '/proc/self/fd/' + str(fd)])
            self.assertEqual(os.fstat(fd).st_ino, (self.root / 'swapfile').stat().st_ino)
            return b'fixture metadata, no formatter invoked\n'
        if args[1:2] == ['show']:
            if 'LoadState' in args:
                return self.load
            return ('FragmentPath=' + str(self.unit()) + '\nDropInPaths=' + self.dropins + '\n').encode()
        if args[1:3] == ['enable', '--now']:
            self.swaps = ('61440 ' + str(self.root / 'swapfile') + '\n').encode()
        if args[1:2] == ['is-active']:
            return self.active
        if args[1:2] == ['is-enabled']:
            return self.enabled
        return b''

    def unit(self):
        return self.root / 'etc/systemd/system' / D.UNIT

    def journal(self, name):
        return self.root / 'journal' / ('host-swap.' + name + '.json')

    def no_format_or_activate(self):
        self.assertFalse(any(args[0] == '/usr/sbin/mkswap' or 'enable' in args for args, _ in self.calls))

    def test_existing_sufficient_system_swap_is_preserved_and_only_metadata_recorded(self):
        self.swaps = b'61440 /foreign.swap\n'
        with mock.patch.object(self.reserve, 'open_roots', side_effect=AssertionError('must not touch foreign paths')):
            self.reserve.prepare()
            self.reserve.inspect()
        self.assertEqual(len(list((self.root / 'journal').iterdir())), 2)
        self.assertFalse((self.root / 'swapfile').exists())
        self.no_format_or_activate()

    def test_existing_insufficient_swap_is_not_replaced_or_augmented(self):
        self.swaps = b'1024 /foreign.swap\n'
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        self.assertEqual(list((self.root / 'journal').iterdir()), [])
        self.no_format_or_activate()

    def test_existing_swap_must_still_be_active_at_final_inspection(self):
        self.swaps = b'61440 /foreign.swap\n'
        self.reserve.prepare()
        self.swaps = b''
        with self.assertRaises(ValueError):
            self.reserve.inspect()
        self.no_format_or_activate()

    def test_new_owned_reserve_is_exclusive_allocated_and_persistent(self):
        self.reserve.prepare()
        info = (self.root / 'swapfile').stat()
        self.assertEqual((info.st_size, stat.S_IMODE(info.st_mode), info.st_nlink), (65536, 0o600, 1))
        self.assertGreaterEqual(info.st_blocks * 512, 65536)
        self.assertEqual(self.unit().read_bytes(), self.raw)
        self.assertEqual(stat.S_IMODE(self.unit().stat().st_mode), 0o644)
        before = (info.st_ino, info.st_mtime_ns)
        self.calls.clear()
        self.reserve.inspect()
        self.no_format_or_activate()
        after = (self.root / 'swapfile').stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), before)

    def test_native_formatter_accepts_only_retained_new_inactive_fixture_descriptor(self):
        def runner(args, **kwargs):
            if args[0] == '/usr/sbin/mkswap':
                self.assertEqual(kwargs['pass_fds'], (int(args[1].rsplit('/', 1)[1]),))
                return D.run(args, **kwargs)
            return self.runner(args, **kwargs)
        self.reserve.run = runner
        self.reserve.prepare()
        self.reserve.inspect()
        # Real mkswap only; kernel activation/systemctl remain fixture commands.
        self.assertEqual((self.root / 'swapfile').stat().st_size, 65536)

    def test_preexisting_file_symlink_fifo_and_unit_never_adopted(self):
        target = self.root / 'swapfile'
        for create in (lambda: target.touch(), lambda: target.symlink_to('/nonexistent'),
                       lambda: os.mkfifo(target, 0o600)):
            create()
            with self.assertRaises(ValueError):
                self.reserve.prepare()
            target.unlink()
        self.unit().write_bytes(self.raw)
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        self.assertEqual(self.unit().read_bytes(), self.raw)
        self.no_format_or_activate()

    def test_generated_unit_unsupported_filesystem_and_low_space_fail_before_creation(self):
        self.load = b'loaded\n'
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        self.load = b'not-found\n'
        self.mount = 'btrfs'
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        self.mount = 'ext4'
        with mock.patch.object(D.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)):
            with self.assertRaises(ValueError):
                self.reserve.prepare()
        self.assertFalse((self.root / 'swapfile').exists())
        self.assertFalse(self.journal('intent').exists())
        self.no_format_or_activate()

    def test_partial_allocation_is_retained_and_not_replayed(self):
        with mock.patch.object(D.os, 'posix_fallocate', side_effect=OSError('interrupted allocation')):
            with self.assertRaises(OSError):
                self.reserve.prepare()
        original = (self.root / 'swapfile').stat().st_ino
        self.assertTrue(self.journal('intent').exists())
        self.assertTrue(self.journal('file').exists())
        self.assertFalse(self.journal('complete').exists())
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        with self.assertRaises(FileNotFoundError):
            self.reserve.inspect()
        self.assertEqual((self.root / 'swapfile').stat().st_ino, original)
        self.no_format_or_activate()

    def test_actual_child_death_preserves_intent_and_inode_without_replay(self):
        code = '''import importlib.util,json,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('dead_swap',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
d.ROOT=Path(sys.argv[2]);d.SIZE=65536;d.MINIMUM=61440
fd=os.open(d.ROOT/'journal',os.O_RDONLY|os.O_DIRECTORY)
def runner(args,**kwargs):
    if args[0]=='/usr/sbin/swapon':return b''
    if args[0]=='/usr/bin/findmnt':return json.dumps({'filesystems':[{'target':str(d.ROOT),'fstype':'ext4'}]}).encode()
    if 'LoadState' in args:return b'not-found\\n'
    raise AssertionError('No formatting or activation allowed')
d.os.posix_fallocate=lambda *args:os._exit(73)
d.Reserve(d.ROOT/'source',fd,runner).prepare()
'''
        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(self.root)],
                               capture_output=True, timeout=20)
        self.assertEqual(child.returncode, 73, child.stderr.decode())
        original = (self.root / 'swapfile').stat().st_ino
        self.assertTrue(self.journal('intent').exists())
        self.assertTrue(self.journal('file').exists())
        self.assertFalse(self.journal('complete').exists())
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        with self.assertRaises(FileNotFoundError):
            self.reserve.inspect()
        self.assertEqual((self.root / 'swapfile').stat().st_ino, original)
        self.no_format_or_activate()

    def test_foreign_effective_dropin_refuses_activation_and_keeps_owned_artifacts(self):
        self.dropins = '/foreign.conf'
        with self.assertRaises(ValueError):
            self.reserve.prepare()
        self.assertTrue((self.root / 'swapfile').exists())
        self.assertTrue(self.unit().exists())
        self.assertFalse(self.journal('complete').exists())
        self.assertFalse(any('enable' in args for args, _ in self.calls))

    def test_replaced_hardlinked_writable_or_sparse_file_refuses_inspection(self):
        self.reserve.prepare()
        target = self.root / 'swapfile'
        target.chmod(0o644)
        with self.assertRaises(ValueError):
            self.reserve.inspect()
        target.chmod(0o600)
        link = self.root / 'link'
        os.link(target, link)
        with self.assertRaises(ValueError):
            self.reserve.inspect()
        link.unlink()
        target.rename(self.root / 'old-swap')
        target.touch(mode=0o600)
        with target.open('r+b') as stream:
            stream.truncate(65536)
        with self.assertRaises(ValueError):
            self.reserve.inspect()

    def test_changed_unit_disabled_or_inactive_reserve_refuses_inspection(self):
        self.reserve.prepare()
        for field in ('active', 'enabled'):
            setattr(self, field, b'inactive\n')
            with self.assertRaises(ValueError):
                self.reserve.inspect()
            setattr(self, field, (field + '\n').encode())
        self.unit().write_bytes(b'foreign unit\n')
        with self.assertRaises(ValueError):
            self.reserve.inspect()

    def test_replayed_preparation_and_changed_source_or_journal_refused(self):
        self.swaps = b'61440 /foreign.swap\n'
        self.reserve.prepare()
        with self.assertRaises(FileExistsError):
            self.reserve.prepare()
        (self.source / D.UNIT).write_bytes(b'changed source\n')
        with self.assertRaises(ValueError):
            self.reserve.inspect()
        (self.source / D.UNIT).write_bytes(self.raw)
        self.journal('complete').chmod(0o644)
        with self.assertRaises(ValueError):
            self.reserve.inspect()

    def test_unprotected_journal_or_systemd_symlink_refused(self):
        (self.root / 'journal').chmod(0o755)
        with self.assertRaises(ValueError):
            D.Reserve(self.source, self.fd, self.runner)
        (self.root / 'journal').chmod(0o700)
        self.unit().parent.rmdir()
        self.unit().parent.symlink_to(self.root / 'source', target_is_directory=True)
        with self.assertRaises(OSError):
            self.reserve.prepare()
        self.no_format_or_activate()


if __name__ == '__main__':
    unittest.main()
