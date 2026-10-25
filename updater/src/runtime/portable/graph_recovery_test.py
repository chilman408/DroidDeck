# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


def load():
    filename = Path(__file__).with_name('graph-recovery.py')
    if not filename.exists():
        raise AssertionError('Graph recovery component must exist')
    spec = importlib.util.spec_from_file_location('recovery', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recovery(unittest.TestCase):
    def fixture(self):
        self.module = load()
        self.state = {'stage': 'ready', 'boot': 'current', 'since': 1}
        self.receipt = None
        self.changes = 0
        self.switches = 0
        self.valid = True
        self.failure = None
        self.locked = False
        @contextmanager
        def lock():
            self.assertFalse(self.locked)
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
        def stage(value):
            self.state = {'stage': value, 'boot': 'current', 'since': self.state['since'] + 1}
            self.changes += 1
        def write(value, exclusive=False):
            if exclusive and self.receipt is not None:
                raise ValueError('Existing receipt')
            self.receipt = dict(value)
        def admit():
            if not self.valid:
                raise ValueError('Foreign baseline')
        def switch():
            self.assertTrue(self.locked, 'Graph mutation must retain original ownership lock')
            self.switches += 1
            if self.failure:
                raise self.failure
        def verify():
            if self.state['stage'] not in ('starting', 'ready'):
                raise ValueError('Wrong verification phase')
            if self.state['stage'] == 'starting':
                stage('ready')
        store = SimpleNamespace(lock=lock, status=lambda: dict(self.state), stage=stage)
        receipts = SimpleNamespace(read=lambda: None if self.receipt is None else dict(self.receipt), write=write)
        return self.module.Recovery(store, receipts, boot=lambda: 'current', admit=admit, switch=switch, verify=verify)

    def test_normal_restart_completes_once_and_cleanup_does_not_replay(self):
        c = self.fixture()
        c.restart()
        self.assertEqual((self.state['stage'], self.receipt['phase'], self.switches), ('ready', 'complete', 1))
        c.recover_pending()
        self.assertEqual(self.switches, 1)
        with self.assertRaises(ValueError):
            c.restart()

    def test_interrupted_graph_is_reconstructed_by_cleanup(self):
        c = self.fixture()
        self.failure = RuntimeError('Simulated stop after quiescence')
        with self.assertRaises(RuntimeError):
            c.restart()
        self.assertEqual((self.state['stage'], self.receipt['phase']), ('starting', 'restarting'))
        self.failure = None
        c.recover_pending()
        self.assertEqual((self.state['stage'], self.receipt['phase']), ('ready', 'complete'))

    def test_interruption_before_startup_stage_write_still_has_owned_recovery(self):
        c = self.fixture()
        self.receipt = {'version': 1, 'phase': 'armed', 'boot': 'current'}
        c.recover_pending()
        self.assertEqual((self.switches, self.state['stage']), (1, 'ready'))

    def test_kill_after_native_ready_before_receipt_commit_never_restarts_twice(self):
        c = self.fixture()
        self.receipt = {'version': 1, 'phase': 'verifying', 'boot': 'current'}
        c.recover_pending()
        self.assertEqual((self.switches, self.changes, self.receipt['phase']), (0, 0, 'complete'))

    def test_missing_receipt_cleanup_has_no_mutation(self):
        c = self.fixture()
        c.recover_pending()
        self.assertEqual((self.switches, self.changes), (0, 0))

    def test_foreign_or_new_boot_state_never_fights_original_watcher(self):
        for stage, boot in [('recovering', 'current'), ('restored', 'current'), ('starting', 'old')]:
            c = self.fixture()
            self.receipt = {'version': 1, 'phase': 'restarting', 'boot': 'current'}
            self.state.update(stage=stage, boot=boot)
            with self.assertRaises(ValueError):
                c.recover_pending()
            self.assertEqual((self.switches, self.changes), (0, 0))

    def test_foreign_profile_does_not_stop_any_service(self):
        c = self.fixture()
        self.valid = False
        with self.assertRaises(ValueError):
            c.restart()
        self.assertIsNone(self.receipt)
        self.assertEqual((self.switches, self.changes), (0, 0))

    def test_receipt_schema_and_boot_binding_reject_foreign_cleanup(self):
        for receipt in ({'version': 2, 'phase': 'armed', 'boot': 'current'},
                        {'version': 1, 'phase': 'armed', 'boot': 'old'},
                        {'version': 1, 'phase': 'unknown', 'boot': 'current'}):
            c = self.fixture()
            self.receipt = receipt
            with self.assertRaises(ValueError):
                c.recover_pending()
            self.assertEqual(self.switches, 0)

    def test_cli_rejects_arbitrary_paths_and_commands(self):
        module = load()
        for args in ([], ['--restart', '/override'], ['--apply'], ['--recover-pending', '--force']):
            with self.assertRaises(ValueError):
                module.main(args)

    def test_recovery_worker_is_the_one_original_watcher_can_quiesce(self):
        self.assertEqual(load().UNIT, 'droiddeck-gpu-activation.service')

    def test_watcher_must_be_live_and_generation_stable(self):
        module = load()
        state = {'ActiveState': 'active', 'MainPID': '123', 'ControlPID': '0'}
        self.assertEqual(module.require_guard(state, '456'), ('123', '456'))
        for bad in ({**state, 'ActiveState': 'failed'}, {**state, 'MainPID': '0'}, {**state, 'ControlPID': '9'}):
            with self.assertRaises(ValueError):
                module.require_guard(bad, '456')

    def test_effective_cleanup_must_finish_before_watchers_stop_timeout(self):
        module = load()
        record = module.expected_supervision()
        self.assertEqual({key: record[key] for key in ('KillSignal', 'FinalKillSignal', 'SendSIGKILL', 'TimeoutStopFailureMode')},
                         {'KillSignal': 9, 'FinalKillSignal': 9, 'SendSIGKILL': True, 'TimeoutStopFailureMode': 'kill'})
        module.validate_supervision(record)
        for field, value in [('TimeoutStopUSec', 180000000), ('KillMode', 'process'), ('Restart', 'always'), ('ExecStopPost', [])]:
            with self.assertRaises(ValueError):
                module.validate_supervision({**record, field: value})


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Requires Linux root filesystem semantics')
class ProtectedFiles(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.directory = tempfile.TemporaryDirectory(prefix='droiddeck-graph-test-', dir='/run')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.module.STATE = self.root
        self.file = self.root / 'source'
        self.file.write_bytes(b'owned source')
        self.file.chmod(0o600)

    def test_protected_reader_accepts_only_regular_owned_generation(self):
        self.assertEqual(self.module.protected_read(self.file), b'owned source')
        with self.assertRaises(ValueError):
            self.module.protected_read(self.file, 2)
        self.file.chmod(0o666)
        with self.assertRaises(ValueError):
            self.module.protected_read(self.file)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            self.module.protected_read(fifo)

    def test_symlink_hardlink_and_writable_parent_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.file)
        with self.assertRaises(OSError):
            self.module.protected_read(link)
        link.unlink()
        os.link(self.file, link)
        with self.assertRaises(ValueError):
            self.module.protected_read(self.file)
        link.unlink()
        self.root.chmod(0o777)
        try:
            with self.assertRaises(ValueError):
                self.module.protected_read(self.file)
        finally:
            self.root.chmod(0o700)

    def test_receipts_are_exclusive_atomic_and_private(self):
        receipts = self.module.Receipts()
        self.assertIsNone(receipts.read())
        value = {'version': 1, 'boot': 'test', 'phase': 'armed'}
        receipts.write(value, exclusive=True)
        self.assertEqual(receipts.read(), value)
        with self.assertRaises(FileExistsError):
            receipts.write(value, exclusive=True)
        value['phase'] = 'complete'
        receipts.write(value)
        self.assertEqual(receipts.read(), value)
        self.assertEqual((self.root / 'receipt.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob('.pending-*')), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
