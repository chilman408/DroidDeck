# SPDX-License-Identifier: GPL-3.0-only
"""Focused orchestration and native filesystem fixtures; never touch real Android."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('gpu_activation', HERE / 'gpu-activation.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
spec = importlib.util.spec_from_file_location('gpu_policy', HERE.parents[1] / 'tools/waydroid-gpu-health-policy.py')
POLICY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(POLICY)


class Lifecycle(unittest.TestCase):
    def fixture(self, stage='prepared'):
        activation = G.Activation.__new__(G.Activation)
        activation.store = Mock()
        activation.store.lock.return_value.__enter__ = Mock()
        activation.store.lock.return_value.__exit__ = Mock(return_value=False)
        activation.store.status.return_value = {'stage': stage, 'boot': 'b' * 36, 'since': 0}
        activation.change = Mock()
        activation.native = Mock(return_value=True)
        return activation

    def test_guard_precedes_mutation(self):
        a = self.fixture()
        with patch.object(G, 'watcher_armed', side_effect=ValueError):
            with self.assertRaises(ValueError):
                a.activate()
        a.change.assert_not_called()
        a.store.stage.assert_not_called()

    def test_single_use(self):
        for stage in G.STAGES - {'prepared'}:
            a = self.fixture(stage)
            with patch.object(G, 'watcher_armed'), self.assertRaises(ValueError):
                a.activate()
            a.change.assert_not_called()

    def test_activation_order_and_ready(self):
        a = self.fixture()
        events = []
        a.store.stage.side_effect = lambda value: events.append(value)
        a.change.side_effect = lambda: events.append('change')
        with patch.object(G, 'watcher_armed'), patch.object(G, 'command', side_effect=lambda args, **kw: events.append(args[1:])):
            a.activate()
        self.assertEqual(events, ['installing', 'change', 'starting', ['start', G.UNITS[3]],
                                 ['start', G.UNITS[2], G.UNITS[1]], 'verifying-native', 'ready'])

    def test_worker_error_requests_independent_recovery(self):
        a = self.fixture()
        a.change.side_effect = ValueError('private detail')
        with patch.object(G, 'watcher_armed'), self.assertRaises(ValueError):
            a.activate()
        self.assertEqual(a.store.stage.call_args.args, ('recovery-required',))

    def test_recovery_stops_worker_before_lock_and_services(self):
        a = self.fixture()
        events = []
        a.store.lock.side_effect = lambda: events.append('lock') or types.SimpleNamespace()
        with patch.object(G, 'stop_worker', side_effect=ValueError), self.assertRaises(ValueError):
            a.recover()
        self.assertEqual(events, [])
        a.change.assert_not_called()

    def test_recovery_refuses_live_service(self):
        a = self.fixture()
        with patch.object(G, 'stop_worker'), patch.object(G, 'command'), patch.object(G, 'unit', return_value={
                'ActiveState': 'active', 'MainPID': '123', 'ControlPID': '0'}), self.assertRaises(ValueError):
            a.recover()
        a.change.assert_not_called()

    def test_recovery_order(self):
        a = self.fixture()
        events = []
        a.store.stage.side_effect = lambda s: events.append(s)
        a.change.side_effect = lambda **kw: events.append(('change', kw))
        with patch.object(G, 'stop_worker', side_effect=lambda: events.append('worker-stopped')), patch.object(
                G, 'command', side_effect=lambda *args, **kw: events.append('services-stopped')), patch.object(
                G, 'unit', return_value={'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}):
            a.recover()
        self.assertEqual(events, ['worker-stopped', 'services-stopped', 'recovering',
                                 ('change', {'restoring': True}), 'restored'])

    def test_reset_only_quiescent_failed_units_not_unloaded_inactive_units(self):
        a = self.fixture()
        seen = set()
        def state(name):
            first = name not in seen
            seen.add(name)
            return {'ActiveState': 'failed' if first and name == G.UNITS[0] else 'inactive', 'MainPID': '0', 'ControlPID': '0'}
        with patch.object(G, 'stop_worker'), patch.object(G, 'command') as command, patch.object(G, 'unit', side_effect=state):
            a.recover()
        self.assertEqual(command.call_args_list[1].args[0], ['/usr/bin/systemctl', 'reset-failed', G.UNITS[0]])

    def test_foreign_profile_refused(self):
        a = self.fixture()
        a.p, a.c = Mock(), Mock()
        a.profiles = Mock(return_value=(b'cpu', b'gpu'))
        a.manifest = Mock()
        a.p._offline_layer_context.return_value = {}
        with patch.object(G, 'protected_read', return_value=b'foreign'), self.assertRaises(ValueError):
            G.Activation.change(a, restoring=True)
        a.p._exchange_profile.assert_not_called()
        a.p._edit_saved_layer.assert_not_called()

    def test_quiescence_all_fields(self):
        for state in ({'ActiveState': 'stopping', 'MainPID': '0', 'ControlPID': '0'},
                      {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '42'}):
            with self.assertRaises(ValueError):
                G.require_quiet(state)
        G.require_quiet({'ActiveState': 'failed', 'MainPID': '0', 'ControlPID': '0'})

    def test_healthy_has_no_expiry(self):
        self.assertEqual(POLICY.decision('ready', True, 2, 10 ** 9), ('continue', 0))
        self.assertEqual(POLICY.decision('installing', False, 0, 480), ('recover', 0))
        self.assertEqual(POLICY.decision('ready', False, 2, 1), ('recover', 3))

    def test_status_closed(self):
        for value in ({}, {'stage': 'ready', 'boot': 'b' * 36, 'since': float('nan')},
                      {'stage': 'ready', 'boot': 'b' * 36, 'since': True},
                      {'stage': 'unknown', 'boot': 'b' * 36, 'since': 1}):
            with self.assertRaises(ValueError):
                G.validate_status(value)

    def test_watch_healthy_does_not_expire(self):
        a = self.fixture('ready')
        with patch.object(G, 'boot_id', return_value='b' * 36), patch.object(G, 'uptime', return_value=1e9), patch.object(
                G.time, 'sleep', side_effect=InterruptedError), patch.object(a, 'recover') as recover:
            with self.assertRaises(InterruptedError):
                a.watch(POLICY)
        recover.assert_not_called()

    def test_watch_machine_startup_timeout(self):
        a = self.fixture('installing')
        with patch.object(G, 'boot_id', return_value='b' * 36), patch.object(G, 'uptime', return_value=480), patch.object(a, 'recover') as recover:
            a.watch(POLICY)
        recover.assert_called_once()
        a.native.assert_not_called()

    def test_watch_failure_threshold(self):
        a = self.fixture('ready')
        a.native.return_value = False
        with patch.object(G, 'boot_id', return_value='b' * 36), patch.object(G, 'uptime', return_value=10), patch.object(
                G.time, 'sleep'), patch.object(a, 'recover') as recover:
            a.watch(POLICY)
        self.assertEqual(a.native.call_count, 3)
        recover.assert_called_once()


@unittest.skipUnless(os.geteuid() == 0, 'native root filesystem fixtures')
class NativeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='droiddeck-gpu-fixture-', dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('copier_fixture', HERE / 'stage-images.py')
        copier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(copier)
        self.patch = patch.object(G, 'STATE', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store = G.Store(None, copier)

    def test_status_atomic_exclusive_and_abandoned_write(self):
        self.store.stage('prepared', True)
        with self.assertRaises(FileExistsError):
            self.store.stage('prepared', True)
        stale = self.path / '.pending-status.json-crashed'
        stale.write_bytes(b'partial')
        self.store.stage('ready')
        self.assertEqual(self.store.status()['stage'], 'ready')
        self.assertTrue(stale.exists())
        self.assertEqual((self.path / 'status.json').stat().st_mode & 0o777, 0o600)

    def test_symlink_hardlink_mode_rejected(self):
        self.store.stage('prepared', True)
        path = self.path / 'status.json'
        os.link(path, self.path / 'hard')
        with self.assertRaises(ValueError):
            self.store.status()
        (self.path / 'hard').unlink()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.status()
        path.chmod(0o600)
        (self.path / 'link').symlink_to(path)
        with self.assertRaises(OSError):
            G.protected_read(self.path / 'link')

    def test_lock_nonblocking(self):
        with self.store.lock():
            with self.assertRaises(BlockingIOError), self.store.lock():
                pass

    def test_pinned_component_source(self):
        for relative, expected in G.PINS.items():
            self.assertEqual(G.sha((HERE.parents[1] / relative).read_bytes()), expected, relative)


if __name__ == '__main__':
    unittest.main(verbosity=2)
