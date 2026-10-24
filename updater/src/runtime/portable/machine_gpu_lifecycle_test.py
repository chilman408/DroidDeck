# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
L = load('machine_lifecycle', HERE / 'machine-gpu-lifecycle.py')
POLICY = load('verified_health_fixture', HERE.parents[1] / 'tools/waydroid-gpu-health-policy.py')
BOOT = '12345678-1234-1234-1234-123456789abc'


class Model:
    def __init__(self, stage='ready'):
        self.expected = {key: 'a' * 64 for key in L.BINDINGS[:3]}
        self.expected.update(dataDevice=1, dataInode=2)
        self.status = {'kind': 'protected-machine-gpu-lifecycle', 'version': 1, 'stage': stage,
            'revision': 0, 'boot': BOOT, 'since': 0, **self.expected}
        self.now, self.boot, self.healthy = 100, BOOT, True
        self.worker_quiet, self.guest_quiet = False, False
        self.events = []
        self.sources = {'check': lambda: None, 'load': lambda role: POLICY if role == 'health' else None}

    def stopped_worker(self):
        self.events.append('worker-stop')
        self.worker_quiet = True

    def stopped_guest(self):
        self.events.append('guest-stop')
        self.guest_quiet = True

    def restored(self, guard):
        self.events.append('recover')
        guard()
        return {'stage': 'settled', 'ownedDataRestored': True, 'nextAction': None, 'sequence': 5,
            'states': dict.fromkeys(('allocator', 'savedLayer', 'configuration'), 'restored'), **self.expected,
            **dict.fromkeys(('servicesActivated', 'propertiesRegenerated', 'androidOperationsExecuted',
                'independentRecoveryArmed', 'installable', 'readyForActivation'), False)}

    def checkpoint(self, before, sequence):
        if self.status != before or sequence != 5:
            raise ValueError('generated CAS or sequence mismatch')
        self.events.append('checkpoint')
        self.status = {**before, 'revision': before['revision'] + 1, 'stage': 'restored', 'since': self.now}

    def make(self, *, arm=True, **changes):
        kwargs = {'admit': lambda: None, 'read_status': lambda: copy.deepcopy(self.status), 'native': lambda: self.healthy,
            'stop_worker': self.stopped_worker, 'worker_stopped': lambda: self.worker_quiet,
            'stop_guest': self.stopped_guest, 'guest_stopped': lambda: self.guest_quiet, 'recover': self.restored,
            'mark_restored': self.checkpoint, 'notify': lambda: self.events.append('notify'),
            'current_boot': lambda: self.boot, 'clock': lambda: self.now, 'sleep': lambda seconds: self.events.append(('sleep', seconds))}
        watcher = L.Watcher(self.sources, self.expected, **{**kwargs, **changes})
        if arm:
            watcher.arm()
        return watcher


class Lifecycle(unittest.TestCase):
    def test_production_cli_modes_fail_closed(self):
        for args in ([], ['--watch'], ['--work'], ['--recover'], ['--force']):
            with self.assertRaises(ValueError):
                L.main(args)

    def test_closed_independent_binding_and_callbacks(self):
        for changed in ({'dataInode': True}, {'metadataSha256': 'A' * 64}, {'unknown': False}):
            m = Model()
            m.expected.update(changed)
            with self.assertRaises(ValueError):
                m.make()
            self.assertEqual(m.events, [])
        with self.assertRaises(ValueError):
            Model().make(native=None)

    def test_closed_status_schema_and_binding_precede_notify(self):
        for changed in ({'version': True}, {'stage': 'foreign'}, {'revision': True}, {'revision': -1},
                        {'boot': 'foreign'}, {'since': float('nan')}, {'since': float('inf')}, {'since': 101},
                        {'dataInode': True}, {'metadataSha256': 'f' * 64}, {'extra': False}):
            m = Model()
            m.status.update(changed)
            with self.assertRaises(ValueError):
                m.make()
            self.assertEqual(m.events, [])

    def test_readiness_single_use_and_terminal_state_does_not_notify(self):
        m = Model()
        w = m.make()
        self.assertEqual(m.events, ['notify'])
        with self.assertRaises(ValueError):
            w.arm()
        restored = Model('restored')
        self.assertEqual(restored.make(arm=False).run()['action'], 'stop')
        self.assertEqual(restored.events, [])

    def test_healthy_ready_has_no_expiry_even_far_past_startup_limit(self):
        m = Model()
        w = m.make()
        for now in (1000, 1000000, 1000000000, 900000000000):
            m.now = now
            self.assertEqual(w.tick()['action'], 'continue')
            self.assertEqual(w.failures, 0)
        self.assertEqual(m.events, ['notify'])

    def test_three_failures_recover_in_order_and_checkpoint_once(self):
        m = Model()
        m.healthy = False
        w = m.make()
        self.assertEqual(w.tick()['action'], 'continue')
        self.assertEqual(w.tick()['action'], 'continue')
        result = w.tick()
        self.assertEqual(result['action'], 'recovered')
        self.assertTrue(result['ownedDataRestored'])
        self.assertTrue(result['servicesLeftStopped'])
        self.assertEqual(m.events, ['notify', 'worker-stop', 'guest-stop', 'recover', 'checkpoint'])
        for flag in ('independentRecoveryArmed', 'servicesActivated', 'propertiesRegenerated',
                     'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])
        with self.assertRaises(ValueError):
            w.tick()
        self.assertEqual(m.events.count('recover'), 1)

    def test_healthy_sample_resets_failures_and_exceptions_count_as_failures(self):
        m = Model()
        m.healthy = False
        w = m.make()
        w.tick()
        w.tick()
        m.healthy = True
        self.assertEqual(w.tick()['failures'], 0)
        m.healthy = False
        self.assertEqual(w.tick()['failures'], 1)
        self.assertNotIn('worker-stop', m.events)
        m = Model()
        def timeout():
            raise subprocess.TimeoutExpired('generated native probe', 5)
        w = m.make(native=timeout)
        self.assertEqual([w.tick()['action'] for _ in range(3)], ['continue', 'continue', 'recovered'])

    def test_startup_limit_and_explicit_recovery_do_not_probe_native(self):
        for stage in ('prepared', 'starting', 'recovery-required'):
            m = Model(stage)
            w = m.make(native=lambda: self.fail('must not probe before ready'))
            if stage != 'recovery-required':
                m.now = POLICY.STARTUP_LIMIT_SEC - 1
                self.assertEqual(w.tick()['action'], 'continue')
                m.now += 1
            self.assertEqual(w.tick()['action'], 'recovered')

    def test_probe_race_discards_stale_result_and_resets_counter(self):
        m = Model()
        def changed():
            m.status.update(stage='recovery-required', revision=1, since=m.now)
            return False
        w = m.make(native=changed)
        self.assertEqual(w.tick()['action'], 'retry')
        self.assertEqual(w.failures, 0)
        self.assertEqual(m.events, ['notify'])
        self.assertEqual(w.tick()['action'], 'recovered')

    def test_regression_or_same_revision_mutation_refuses_without_stop(self):
        for key, value in (('stage', 'starting'), ('revision', -1), ('since', -1)):
            m = Model()
            w = m.make()
            m.status[key] = value
            with self.assertRaises(ValueError):
                w.tick()
            self.assertEqual(m.events, ['notify'])

    def test_clock_boot_and_admission_loss_refuse_before_stopping(self):
        for failure in ('clock', 'boot', 'admit', 'source'):
            m = Model('recovery-required')
            denied = [False]
            def admit():
                if denied[0]:
                    raise ValueError('generated admission changed')
            m.sources['check'] = admit
            w = m.make(admit=admit)
            if failure == 'clock':
                m.now = 99
            elif failure == 'boot':
                m.boot = '87654321-1234-1234-1234-123456789abc'
            else:
                denied[0] = True
            with self.assertRaises(ValueError):
                w.tick()
            self.assertEqual(m.events, ['notify'])

    def test_stop_failure_or_unproven_quiescence_never_enters_data_recovery(self):
        for failure in ('stop-worker', 'worker-busy', 'worker-not-bool', 'guest-busy'):
            m = Model('recovery-required')
            overrides = {}
            if failure == 'stop-worker':
                def fail():
                    m.events.append('worker-stop')
                    raise OSError('generated stop failure')
                overrides['stop_worker'] = fail
            elif failure.startswith('worker'):
                overrides['worker_stopped'] = lambda: 1 if failure == 'worker-not-bool' else False
            else:
                overrides['guest_stopped'] = lambda: False
            w = m.make(**overrides)
            with self.assertRaises(L.LifecycleUncertain):
                w.tick()
            self.assertNotIn('recover', m.events)
            self.assertNotIn('checkpoint', m.events)
            if failure != 'guest-busy':
                self.assertNotIn('guest-stop', m.events)
            with self.assertRaises(ValueError):
                w.tick()

    def test_lifecycle_change_after_worker_stop_preserves_guest(self):
        m = Model('recovery-required')
        def stopped():
            m.stopped_worker()
            m.status.update(revision=1, stage='ready', since=m.now)
        w = m.make(stop_worker=stopped)
        with self.assertRaises(L.LifecycleUncertain):
            w.tick()
        self.assertEqual(m.events, ['notify', 'worker-stop'])

    def test_unsettled_foreign_or_activated_recovery_result_never_checkpoints(self):
        for changed in ({'ownedDataRestored': False}, {'sequence': True}, {'dataInode': 3},
                        {'servicesActivated': True}, {'states': {'allocator': 'selected'}}, {'stage': 'ready'}):
            m = Model('recovery-required')
            def recovered(guard):
                return {**m.restored(guard), **changed}
            w = m.make(recover=recovered)
            with self.assertRaises(L.LifecycleUncertain):
                w.tick()
            self.assertNotIn('checkpoint', m.events)

    def test_completion_write_failure_or_missing_checkpoint_is_uncertain(self):
        for published in (False, True):
            m = Model('recovery-required')
            def failed(before, sequence):
                if published:
                    m.checkpoint(before, sequence)
                    raise OSError('generated postpublication failure')
            w = m.make(mark_restored=failed)
            with self.assertRaises(L.LifecycleUncertain):
                w.tick()
            self.assertEqual(m.status['stage'], 'restored' if published else 'recovery-required')
            with self.assertRaises(ValueError):
                w.tick()

    def test_revision_capacity_checked_before_any_stop(self):
        m = Model('recovery-required')
        m.status['revision'] = 2**63 - 1
        w = m.make()
        with self.assertRaises(ValueError):
            w.tick()
        self.assertEqual(m.events, ['notify'])

    def test_loop_sleeps_between_samples_without_a_healthy_deadline(self):
        m = Model()
        m.healthy = False
        w = m.make(arm=False)
        self.assertEqual(w.run()['action'], 'recovered')
        self.assertEqual([event for event in m.events if type(event) is tuple], [('sleep', 15), ('sleep', 15)])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated lifecycle/controller fixtures required')
class NativeLifecycle(unittest.TestCase):
    def fixture(self, stack):
        module = load('lifecycle_peer_fixtures', HERE / 'machine_gpu_peer_policy_test.py')
        peer = module.HeldPeerRecipes()
        peer.setUp()
        self.addCleanup(peer.doCleanups)
        sources = peer.f.hold(stack)
        admitted = module.S.hold_peer_recipe(peer.copier, stack, sources, peer.root,
            read_effective=lambda _projector: copy.deepcopy(peer.records))
        controller, p = peer.f.peers.recovery()
        m = Model('recovery-required')
        observed = controller.status_fixture.status()
        m.expected = {key: observed[key] for key in L.BINDINGS}
        m.status.update(m.expected)
        m.sources = sources
        return m, admitted, peer, controller, p

    def test_watcher_drives_existing_owned_controller_and_records_completion(self):
        with ExitStack() as stack:
            m, admitted, _peer, controller, _p = self.fixture(stack)
            w = m.make(admit=admitted['check'], recover=lambda guard: controller.run_recovery(supervised=guard))
            result = w.tick()
        self.assertEqual(result['action'], 'recovered')
        self.assertEqual(result['recoverySequence'], 5)
        self.assertEqual(json.loads(controller.journal.record.read_bytes())['sequence'], 5)
        self.assertEqual(m.status['stage'], 'restored')
        self.assertEqual(m.events, ['notify', 'worker-stop', 'guest-stop', 'checkpoint'])

    def test_policy_loss_during_owned_recovery_retains_unsaved_progress(self):
        with ExitStack() as stack:
            m, admitted, peer, controller, p = self.fixture(stack)
            rename = p.rename_exclusive
            def changed(*args, **kwargs):
                rename(*args, **kwargs)
                peer.records['droiddeck-machine-gpu-worker.service']['KillMode'] = 'process'
            w = m.make(admit=admitted['check'], recover=lambda guard: controller.run_recovery(supervised=guard))
            with mock.patch.object(p, 'rename_exclusive', side_effect=changed), self.assertRaises(L.LifecycleUncertain):
                w.tick()
        self.assertEqual(json.loads(controller.journal.record.read_bytes())['sequence'], 0)
        self.assertTrue(controller.status_fixture.status()['checkpointRequired'])
        self.assertNotIn('checkpoint', m.events)
        self.assertEqual(m.status['stage'], 'recovery-required')


if __name__ == '__main__':
    unittest.main()
