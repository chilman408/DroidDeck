# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
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
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('cpu_recovery_tested', Path(__file__).with_name('cpu-recovery.py'))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
BOOT = '01234567-1234-1234-1234-123456789abc'
OTHER_BOOT = '87654321-1234-1234-1234-123456789abc'
QUIET = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
GOOD = {'kind': 'android-cpu-fallback-health', 'cpuFallbackVerified': True, 'nativeGpuVerified': False}


class Store:
    def __init__(self):
        self.gpu_status = {'stage': 'restored', 'boot': OTHER_BOOT, 'since': 42}
        self.records, self.writes, self.depth = {}, [], 0

    @contextmanager
    def lock(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def status(self):
        return dict(self.gpu_status)

    def read(self, name):
        if name not in self.records:
            raise FileNotFoundError(name)
        return self.records[name]

    def write(self, name, value, exclusive=False):
        if self.depth != 1:
            raise AssertionError('Every fallback record must hold the original transaction lock')
        if exclusive and name in self.records:
            raise FileExistsError(name)
        self.writes.append((name, value['stage'], exclusive))
        self.records[name] = value


class Fixture(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.a = SimpleNamespace(store=self.store, metadata=Mock(), SESSION_START_TIMEOUT=210)
        self.commands = []
        self.g = SimpleNamespace(boot_id=lambda: BOOT, WORKER='original-worker', WATCHER='original-watcher',
            WORK=Path('/generated/waydroid'), ROOT=Path('/generated/recovery'), CPU='original-cpu-pin',
            UNITS=('runtime', 'session', 'container', 'compositor'),
            protected_read=Mock(return_value=b'original cpu bytes'), sha=Mock(return_value='original-cpu-pin'),
            time=SimpleNamespace(monotonic=Mock(return_value=0), sleep=Mock()), subprocess=subprocess)
        self.g.unit = Mock(side_effect=lambda name: {'ActiveState': 'active', 'MainPID': str(os.getpid()), 'ControlPID': '0'}
                           if name == self.g.WATCHER else dict(QUIET))
        def quiet(value):
            if value != QUIET:
                raise ValueError('Not quiet')
        self.g.require_quiet = quiet
        def command(args, **kwargs):
            self.commands.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(GOOD).encode())
        self.g.command = Mock(side_effect=command)
        self.r = C.Recovery(self.a, self.g)
        self.r.prove_restored = Mock()

    def record(self, stage, **changes):
        self.store.records[self.r.record] = {'version': 1, 'boot': BOOT, 'stage': stage, **changes}

    def starts(self):
        return [args for args, _ in self.commands if args[:2] == ['/usr/bin/systemctl', 'start']]


class Recovery(Fixture):
    def test_success_is_software_verified_before_runtime_and_original_gpu_status_is_preserved(self):
        original = self.store.status()
        self.assertTrue(self.r.start())
        self.assertEqual(self.starts(), [
            ['/usr/bin/systemctl', 'start', 'compositor'],
            ['/usr/bin/systemctl', 'start', 'container', 'session'],
            ['/usr/bin/systemctl', 'start', '--no-block', 'runtime']])
        self.assertEqual([(stage, exclusive) for _, stage, exclusive in self.store.writes], [('starting', True), ('ready', False)])
        self.assertEqual(self.store.status(), original)
        self.r.prove_restored.assert_called_once_with()
        self.assertFalse(any('upgrade' in args or '--activate' in args or '--admit' in args for args, _ in self.commands))

    def test_live_mutator_or_nonwatcher_cannot_start_or_write_records(self):
        for name, invalid in [(self.g.WORKER, {'ActiveState': 'active', 'MainPID': '2', 'ControlPID': '0'}),
                              (self.g.WATCHER, {'ActiveState': 'active', 'MainPID': '2', 'ControlPID': '0'})]:
            original = self.g.unit.side_effect
            self.g.unit.side_effect = lambda selected, name=name, invalid=invalid: invalid if selected == name else original(selected)
            with self.assertRaises(ValueError):
                self.r.start()
            self.g.unit.side_effect = original
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_foreign_profile_metadata_or_nonrestored_state_refuses_before_start(self):
        for stage in ('prepared', 'ready', 'recovering', 'recovery-required'):
            self.store.gpu_status['stage'] = stage
            with self.assertRaises(ValueError):
                self.r.start()
        self.store.gpu_status['stage'] = 'restored'
        self.g.sha.return_value = 'foreign'
        with self.assertRaises(ValueError):
            self.r.start()
        self.g.sha.return_value = self.g.CPU
        self.a.metadata.side_effect = ValueError('foreign account')
        with self.assertRaises(ValueError):
            self.r.start()
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_uncertain_or_failed_same_boot_attempt_is_stopped_not_replayed(self):
        for stage in ('starting', 'failed'):
            self.record(stage)
            self.assertFalse(self.r.start())
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.r.status(), 'failed')
        self.assertTrue(all(args == ['/usr/bin/systemctl', 'stop', *self.g.UNITS] for args, _ in self.commands))

    def test_ready_same_boot_does_not_repeat_start(self):
        self.record('ready')
        self.assertTrue(self.r.start())
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_new_boot_attempt_retains_previous_boot_record(self):
        previous = 'cpu-fallback-' + OTHER_BOOT + '.json'
        self.store.records[previous] = {'version': 1, 'boot': OTHER_BOOT, 'stage': 'failed'}
        self.assertTrue(self.r.start())
        self.assertEqual(self.store.records[previous]['stage'], 'failed')
        self.assertEqual(len(self.store.records), 2)

    def test_bad_perboot_receipts_never_get_adopted(self):
        for invalid in [{}, [], {'version': True, 'boot': BOOT, 'stage': 'ready'},
                        {'version': 1, 'boot': OTHER_BOOT, 'stage': 'ready'},
                        {'version': 1, 'boot': BOOT, 'stage': 'ready', 'extra': True},
                        {'version': 1, 'boot': BOOT, 'stage': 'foreign'}]:
            self.store.records[self.r.record] = invalid
            with self.assertRaises(ValueError):
                self.r.start()
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_proof_failure_leaves_profile_and_original_status_and_no_partial_receipt(self):
        self.r.prove_restored.side_effect = ValueError('foreign allocator')
        with self.assertRaises(ValueError):
            self.r.start()
        self.assertEqual((self.commands, self.store.writes), ([], []))
        self.assertEqual(self.store.gpu_status['stage'], 'restored')

    def test_finite_readiness_failure_stops_graph_without_exposing_runtime(self):
        self.g.time.monotonic.side_effect = [0, 121]
        with self.assertRaises(ValueError):
            self.r.start()
        self.assertEqual(self.r.status(), 'failed')
        self.assertNotIn(['/usr/bin/systemctl', 'start', '--no-block', 'runtime'], self.starts())
        self.assertEqual(self.commands[-1][0], ['/usr/bin/systemctl', 'stop', *self.g.UNITS])

    def test_start_exception_is_bounded_and_preserves_failed_receipt(self):
        original = self.g.command.side_effect
        def fail(args, **kwargs):
            if args == ['/usr/bin/systemctl', 'start', 'compositor']:
                raise OSError('generated startup failure')
            return original(args, **kwargs)
        self.g.command.side_effect = fail
        with self.assertRaises(OSError):
            self.r.start()
        self.assertEqual(self.r.status(), 'failed')
        self.assertEqual(self.starts(), [])

    def test_failed_probe_is_retried_within_deadline_not_accepted_as_gpu_success(self):
        self.r.healthy = Mock(side_effect=[ValueError('unstable generation'), False, True])
        self.assertTrue(self.r.start())
        self.assertEqual(self.g.time.sleep.call_args_list, [((5,),), ((5,),)])

    def test_health_requires_exact_cpu_result_not_zero_exit_or_gpu(self):
        for value in ({}, [], True, {**GOOD, 'cpuFallbackVerified': False},
                      {**GOOD, 'nativeGpuVerified': True}, {**GOOD, 'nativeGpuVerified': 'false'}):
            self.g.command.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())
            self.g.command.side_effect = None
            self.assertFalse(self.r.healthy())
        self.g.command.return_value = SimpleNamespace(returncode=0, stdout=b'x' * 16385)
        self.assertFalse(self.r.healthy())

    def test_healthy_watcher_has_no_expiry_or_periodic_restart(self):
        self.record('ready')
        self.r.healthy = Mock(return_value=True)
        self.g.time.sleep.side_effect = [None, None, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.r.watch()
        self.assertEqual(self.r.healthy.call_count, 3)
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_three_bad_samples_stop_once_and_keep_original_gpu_status(self):
        self.record('ready')
        self.r.healthy = Mock(side_effect=[False, True, False, False, False])
        self.r.watch()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][0], ['/usr/bin/systemctl', 'stop', *self.g.UNITS])
        self.assertEqual(self.r.status(), 'failed')
        self.assertEqual(self.store.gpu_status['stage'], 'restored')

    def test_refused_boot_identity_cannot_select_arbitrary_receipt_paths(self):
        for boot in ('../foreign', '0' * 36, BOOT.upper(), ''):
            self.g.boot_id = lambda: boot
            with self.assertRaises(ValueError):
                C.Recovery(self.a, self.g)


class Proof(Fixture):
    def setUp(self):
        super().setUp()
        del self.r.prove_restored
        self.ctx = {'property': Path('/generated/data/property'), 'dataDevice': 1, 'dataInode': 2, 'check': Mock()}
        self.a.profiles = Mock()
        self.a.offline = Mock(return_value=self.ctx)
        self.a.manifest = Mock()
        self.a.selected = Mock(return_value='absent')
        self.g.ALLOC, self.g.READY, self.g.RESTORED, self.g.SCOPE = 'generated-allocator', 'ready', 'restored', 'generated'

    def retained(self):
        self.info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=3, st_ino=4)
        self.a.c = SimpleNamespace(open_chain=Mock(return_value=[(Path('/generated/misc'), 9)]),
                                   check_chain=Mock(), check_image=Mock())
        self.a.p = SimpleNamespace(ALLOCATOR_CODE=b'original allocator\n', _allocator_metadata=Mock(),
            _read_profile_file=Mock(return_value=(10, self.info, b'original allocator\n')))
        self.g.protected_read.return_value = b'{"fileDevice":3,"fileInode":4}'
        return patch.object(C.os.path, 'lexists', side_effect=lambda path: path.name in ('restored', 'generated-allocator'))

    def test_cpu_and_no_allocator_state_must_still_prove_guest_data_manifest_and_layer(self):
        with patch.object(C.os.path, 'lexists', return_value=False):
            self.r.prove_restored()
        self.a.profiles.assert_called_once_with()
        self.a.offline.assert_called_once()
        self.a.manifest.assert_called_once_with(self.ctx)
        self.ctx['check'].assert_called_once_with()
        self.assertEqual(self.commands, [])

    def test_saved_layer_or_live_graph_or_partial_allocator_refuses(self):
        self.a.selected.return_value = 'true'
        with self.assertRaises(ValueError):
            self.r.prove_restored()
        self.a.selected.return_value = 'absent'
        with patch.object(C.os.path, 'lexists', side_effect=lambda path: path.name == 'ready'):
            with self.assertRaises(ValueError):
                self.r.prove_restored()
        self.assertEqual(self.commands, [])

    def test_retained_allocator_requires_original_metadata_inode_bytes_and_current_generation(self):
        with self.retained(), patch.object(C.os, 'fstat', return_value='misc-identity'):
            self.r.prove_restored()
        self.a.p._allocator_metadata.assert_called_once_with({'fileDevice': 3, 'fileInode': 4},
            {'dataDevice': 1, 'dataInode': 2}, 'misc-identity', 'generated')
        self.a.c.check_image.assert_called_once_with(9, 'restored', 10, self.info, private=True)
        self.a.c.check_chain.assert_called_once()
        self.ctx['check'].assert_called_once()
        self.assertEqual(self.commands, [])

    def test_foreign_retained_inode_bytes_or_metadata_refuses_without_mutation(self):
        for change in ('inode', 'bytes', 'metadata'):
            with self.retained(), patch.object(C.os, 'fstat', return_value='misc-identity'):
                if change == 'inode':
                    self.info.st_ino = 5
                elif change == 'bytes':
                    self.a.p._read_profile_file.return_value = (10, self.info, b'foreign allocator\n')
                else:
                    self.a.p._allocator_metadata.side_effect = ValueError('foreign receipt')
                with self.assertRaises(ValueError):
                    self.r.prove_restored()
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_pending_allocator_or_remaining_native_settings_refuses(self):
        for selected in ('pending-generated-allocator', 'waydroid_settings', 'ready'):
            with self.retained(), patch.object(C.os.path, 'lexists',
                    side_effect=lambda path: path.name in ('restored', 'generated-allocator', selected)):
                with self.assertRaises(ValueError):
                    self.r.prove_restored()
        self.assertEqual((self.commands, self.store.writes), ([], []))

    def test_active_graph_is_not_stopped_or_adopted_for_initial_fallback_proof(self):
        self.g.unit.return_value = {'ActiveState': 'active', 'MainPID': '900', 'ControlPID': '0'}
        self.g.unit.side_effect = None
        with self.assertRaises(ValueError):
            self.r.prove_restored()
        self.assertEqual((self.commands, self.store.writes), ([], []))


def native_store(path):
    spec = importlib.util.spec_from_file_location('cpu_native_store', Path(__file__).with_name('gpu-activation.py'))
    gpu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gpu)
    gpu.STATE = Path(path)

    class Copier:
        def open_chain(self, stack, path, **kwargs):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            return [(Path(path), fd, info.st_dev, info.st_ino)]

        def protected(self, info, mode):
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, mode):
                raise ValueError('Fixture requires protected original inode')

        def write_all(self, fd, raw):
            while raw:
                raw = raw[os.write(fd, raw):]

        def check_chain(self, chain, **kwargs):
            for path, fd, device, inode in chain:
                for info in (path.lstat(), os.fstat(fd)):
                    self.protected(info, 0o700)
                    if (info.st_dev, info.st_ino) != (device, inode):
                        raise ValueError('Fixture chain changed')

    return gpu.Store(None, Copier())


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux durable recovery fixtures')
class Native(Fixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix='droiddeck-cpu-record-', dir='/root')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.real = native_store(self.root)
        self.original = json.dumps(self.store.gpu_status).encode() + b'\n'
        (self.root / 'status.json').write_bytes(self.original)
        (self.root / 'status.json').chmod(0o600)
        self.a.store = self.real

    def test_actual_exclusive_durable_start_record_and_replacement_preserve_gpu_journal(self):
        self.assertTrue(self.r.start())
        info = (self.root / self.r.record).stat()
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, 0o600, 1))
        self.assertEqual(self.r.status(), 'ready')
        self.assertEqual((self.root / 'status.json').read_bytes(), self.original)
        with self.real.lock(), self.assertRaises(FileExistsError):
            self.r.stage('starting', exclusive=True)

    def test_actual_child_death_after_started_record_cannot_replay_same_boot(self):
        code = '''import importlib.util,os,sys
s=importlib.util.spec_from_file_location('fixture',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
store=m.native_store(sys.argv[2])
with store.lock(): store.write(sys.argv[3],{'version':1,'boot':m.BOOT,'stage':'starting'},True)
os._exit(73)
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, __file__, str(self.root), self.r.record], timeout=20)
        self.assertEqual(result.returncode, 73)
        self.assertEqual(self.r.status(), 'starting')
        self.assertFalse(self.r.start())
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.r.status(), 'failed')
        self.assertEqual((self.root / 'status.json').read_bytes(), self.original)

    def test_actual_fifo_symlink_hardlink_or_writable_record_refused_without_start(self):
        target = self.root / self.r.record
        os.mkfifo(target, mode=0o600)
        with self.assertRaises(ValueError): self.r.start()
        target.unlink()
        original = self.root / 'original.json'
        original.write_text(json.dumps({'version': 1, 'boot': BOOT, 'stage': 'ready'}))
        original.chmod(0o600)
        target.symlink_to(original)
        with self.assertRaises(OSError): self.r.start()
        target.unlink()
        os.link(original, target)
        with self.assertRaises(ValueError): self.r.start()
        target.unlink()
        target.write_text(original.read_text())
        target.chmod(0o666)
        with self.assertRaises(ValueError): self.r.start()
        self.assertEqual(self.commands, [])

    def test_actual_mutator_lock_cannot_be_bypassed(self):
        with self.real.lock(), self.assertRaises(BlockingIOError):
            self.r.start()
        self.assertEqual(self.commands, [])
        self.assertFalse((self.root / self.r.record).exists())


if __name__ == '__main__':
    unittest.main()
