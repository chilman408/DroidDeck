# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fresh_gpu_tested', Path(__file__).with_name('fresh-gpu.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class Admission(unittest.TestCase):
    def test_unique_closed_transaction_names(self):
        F.configure('012345abcdef')
        self.assertEqual(F.G.SCOPE, 'fresh-012345abcdef')
        self.assertEqual(F.G.ALLOC, 'mesa-allocator-fresh-012345abcdef')
        self.assertEqual(F.G.READY, 'pending-droiddeck-allocator-fresh-012345abcdef-ready')
        self.assertEqual(F.G.WORKER, 'droiddeck-fresh-gpu-worker@012345abcdef.service')
        self.assertEqual(str(F.G.STATE), '/var/lib/droiddeck/gpu-transactions/012345abcdef')
        for bad in ('', 'a' * 11, 'A' * 12, '../0123456789', '0' * 13, ['0' * 12]):
            with self.assertRaises(ValueError):
                F.configure(bad)

    def test_current_primitive_sources_are_independently_pinned(self):
        root = Path(__file__).parents[2]
        for name, pin in F.G.PINS.items():
            self.assertEqual(F.G.sha((root / name).read_bytes()), pin)

    def test_worker_never_replays_at_boot_and_watcher_is_ready_before_worker(self):
        worker = Path(__file__).with_name('droiddeck-fresh-gpu-worker@.service').read_text()
        watcher = Path(__file__).with_name('droiddeck-fresh-gpu-watch@.service').read_text()
        self.assertIn('Requires=droiddeck-fresh-gpu-watch@%i.service', worker)
        self.assertIn('BindsTo=droiddeck-fresh-gpu-watch@%i.service', worker)
        self.assertNotIn('[Install]', worker)
        self.assertIn('Restart=no', worker)
        self.assertIn('Type=notify', watcher)
        self.assertNotIn('RuntimeMaxSec', watcher)


class BootResume(unittest.TestCase):
    def test_configured_android_deadline_is_bounded_and_waiter_outlives_session(self):
        self.assertGreater(F.Fresh.SESSION_START_TIMEOUT, 180)
        self.assertLess(F.Fresh.SESSION_START_TIMEOUT, 450)

    def operation(self, *, stage='starting', boot='previous-boot', profile=None, worker=None,
                  native=True, failure=None):
        self.state = {'stage': stage, 'boot': boot}
        self.stages, self.commands = [], []
        def change(value):
            self.stages.append(value)
            self.state.update(stage=value, boot='current-boot')
        activation = SimpleNamespace(
            store=SimpleNamespace(lock=lambda: nullcontext(), status=lambda: dict(self.state), stage=change),
            metadata=lambda: None, native=lambda: native, SESSION_START_TIMEOUT=F.Fresh.SESSION_START_TIMEOUT)
        def command(args, **kwargs):
            self.commands.append(args)
            if failure:
                raise failure
        worker = worker or {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
        with patch.object(F.G, 'boot_id', return_value='current-boot'), \
                patch.object(F.G, 'unit', return_value=worker), \
                patch.object(F.G, 'protected_read', return_value=b'generated-profile'), \
                patch.object(F.G, 'sha', return_value=F.G.GPU if profile is None else profile), \
                patch.object(F.G, 'command', side_effect=command):
            F.Fresh.resume_boot(activation)

    def test_ready_and_interrupted_post_mutation_stages_resume_without_activation_replay(self):
        for stage in ('ready', 'starting', 'verifying-native'):
            self.operation(stage=stage)
            self.assertEqual(self.stages, ['starting', 'ready'])
            self.assertEqual(self.commands, [
                ['/usr/bin/systemctl', 'start', F.G.UNITS[3], F.G.UNITS[2], F.G.UNITS[1]],
                ['/usr/bin/systemctl', 'start', '--no-block', F.G.UNITS[0]]])

    def test_same_boot_and_mutating_or_restored_states_are_not_rearmed(self):
        for stage, boot in [('starting', 'current-boot')] + [
                (stage, 'previous-boot') for stage in ('prepared', 'installing', 'recovering', 'restored', 'recovery-required')]:
            self.operation(stage=stage, boot=boot)
            self.assertEqual((self.stages, self.commands), ([], []))

    def test_foreign_profile_and_live_mutator_refuse_resume_before_state_change(self):
        for kwargs in ({'profile': F.G.CPU},
                       {'worker': {'ActiveState': 'active', 'MainPID': '100', 'ControlPID': '0'}}):
            with self.assertRaises(ValueError):
                self.operation(**kwargs)
            self.assertEqual((self.stages, self.commands), ([], []))

    def test_shutdown_interruption_retains_resumable_stage(self):
        with self.assertRaises(OSError):
            self.operation(failure=OSError('generated cancelled startup'))
        self.assertEqual(self.state, {'stage': 'starting', 'boot': 'current-boot'})
        self.assertEqual(self.stages, ['starting'])
        self.operation(stage=self.state['stage'], boot='previous-boot')
        self.assertEqual(self.stages, ['starting', 'ready'])

    def test_unverified_native_startup_never_exposes_runtime(self):
        with patch.object(F.time, 'monotonic', side_effect=[0, 151]):
            self.operation(native=False)
        self.assertEqual(self.stages, ['starting', 'recovery-required'])
        self.assertEqual(len(self.commands), 1)


class SessionRecovery(unittest.TestCase):
    def owner(self, **changes):
        return {'ActiveState': 'activating', 'MainPID': str(os.getpid()), 'ControlPID': '0', **changes}

    def test_only_supervised_main_with_one_running_original_watcher_is_admitted(self):
        rows = [{'unit': 'droiddeck-fresh-gpu-watch@012345abcdef.service', 'active': 'active', 'sub': 'running'}]
        with patch.object(F.G, 'unit', return_value=self.owner()), patch.object(F.G, 'command', return_value=SimpleNamespace(stdout=json.dumps(rows).encode())):
            self.assertEqual(F.session_recovery_token(), '012345abcdef')
            for invalid in ([], rows * 2, [{'unit': 'foreign', 'active': 'active', 'sub': 'running'}], [{**rows[0], 'sub': 'dead'}]):
                with patch.object(F.G, 'command', return_value=SimpleNamespace(stdout=json.dumps(invalid).encode())):
                    with self.assertRaises(ValueError): F.session_recovery_token()
        for owner in (self.owner(ActiveState='active'), self.owner(MainPID='0'), self.owner(MainPID='2', ControlPID='3')):
            with patch.object(F.G, 'unit', return_value=owner):
                with self.assertRaises(ValueError): F.session_recovery_token()
        with patch.object(F.G, 'unit', return_value=self.owner(MainPID='0', ControlPID=str(os.getpid()))), patch.object(F.G, 'command', return_value=SimpleNamespace(stdout=json.dumps(rows).encode())):
            self.assertEqual(F.session_recovery_token(), '012345abcdef')

    def operation(self, *, stage='ready', fail=None, native=True):
        self.events, self.stages = [], []
        store = SimpleNamespace(lock=lambda: nullcontext(), status=lambda: {'stage': stage, 'boot': 'generated-boot'}, stage=self.stages.append)
        activation = SimpleNamespace(store=store, metadata=lambda: self.events.append('metadata'), native=lambda: native,
                                     SESSION_START_TIMEOUT=F.Fresh.SESSION_START_TIMEOUT)
        def command(args, **kwargs):
            self.events.append(args)
            if args == fail: raise OSError('generated failure')
        def unit(name):
            return {'ActiveState': 'active', 'MainPID': '100', 'ControlPID': '0'} if name == F.G.WATCHER else {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
        with patch.object(F.G, 'boot_id', return_value='generated-boot'), patch.object(F.G, 'unit', side_effect=unit), patch.object(F.G, 'command', side_effect=command):
            F.Fresh.restart_session(activation)

    def test_full_graph_quiesces_then_new_compositor_then_container_and_session(self):
        self.operation()
        self.assertEqual(self.stages, ['starting', 'ready'])
        self.assertEqual(self.events, ['metadata',
            ['/usr/bin/systemctl', 'stop', *F.G.UNITS],
            ['/usr/bin/systemctl', 'start', F.G.UNITS[3]],
            ['/usr/bin/systemctl', 'start', F.G.UNITS[2], F.G.UNITS[1]],
            ['/usr/bin/systemctl', 'start', '--no-block', F.G.UNITS[0]]])

    def test_failures_return_ownership_to_independent_guard_without_starting_runtime(self):
        with self.assertRaises(OSError):
            self.operation(fail=['/usr/bin/systemctl', 'start', F.G.UNITS[3]])
        self.assertEqual(self.stages, ['starting', 'recovery-required'])
        self.assertNotIn(['/usr/bin/systemctl', 'start', '--no-block', F.G.UNITS[0]], self.events)
        with self.assertRaises(ValueError): self.operation(stage='restored')
        self.assertEqual((self.stages, self.events), ([], []))

    def test_native_readiness_deadline_never_exposes_unverified_runtime(self):
        with patch.object(F.time, 'monotonic', side_effect=[0, 121]):
            with self.assertRaises(ValueError): self.operation(native=False)
        self.assertEqual(self.stages, ['starting', 'recovery-required'])
        self.assertNotIn(['/usr/bin/systemctl', 'start', '--no-block', F.G.UNITS[0]], self.events)


if __name__ == '__main__':
    unittest.main()
