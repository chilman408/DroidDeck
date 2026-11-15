# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('video_recycle', Path(__file__).with_name('capture-video-recycle.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def unit(name, pid=41, restarts=8, invocation='a' * 32, start=1000):
    return {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running', 'MainPID': str(pid),
            'ControlPID': '0', 'InvocationID': invocation, 'ControlGroup': '/system.slice/' + name,
            'NRestarts': str(restarts), 'ExecMainStartTimestampMonotonic': str(start),
            'Restart': 'always', 'RestartUSec': '2s', 'KillMode': 'control-group',
            'FragmentPath': '/run/systemd/transient/' + name, 'DropInPaths': ''}


class Fake:
    video_unit = R.VIDEO
    def __init__(self):
        self.video = unit(R.VIDEO)
        self.peers = {name: unit(name, pid=100 + i) for i, name in enumerate(R.GUARDS)}
        self.clock, self.signals, self.closed, self.handles, self.sent = 0., [], [], [], False
        self.on_handle, self.on_signal, self.on_observe = lambda: None, lambda: None, lambda: None
        self.new = unit(R.VIDEO, 42, 9, 'b' * 32, 2000)
        self.boot_id = 'b0a62724-6d9b-43fc-8fbd-39a08057832c'
    def observe(self, name):
        self.on_observe()
        return copy.deepcopy(self.video if name == R.VIDEO else self.peers[name])
    def boot(self): return self.boot_id
    def process(self, value): return int(value['MainPID']) * 100
    def handle(self, value):
        self.handles.append(value['MainPID'])
        self.on_handle()
        return 700
    def exited(self, fd): return self.sent
    def terminate(self, fd):
        self.signals.append(fd)
        self.sent = True
        self.video = {**self.video, 'ActiveState': 'activating', 'SubState': 'auto-restart', 'MainPID': '0'}
        self.on_signal()
    def close(self, fd): self.closed.append(fd)
    def now(self): return self.clock
    def sleep(self):
        self.clock += .1
        if self.clock >= .3 and self.video['ActiveState'] == 'activating': self.video = copy.deepcopy(self.new)


class RecycleTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(R.sys, 'platform', 'linux')
        self.uid = patch.object(R.os, 'geteuid', return_value=0, create=True)
        self.platform.start(); self.uid.start()
        self.addCleanup(self.platform.stop); self.addCleanup(self.uid.stop)

    def test_one_pidfd_signal_fresh_invocation_and_all_peer_guards(self):
        fake = Fake()
        with R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
            receipt = recycle.run(lambda value: value['MainPID'] == '42')
            self.assertEqual(receipt['before']['MainPID'], '41')
            self.assertEqual(receipt['after']['MainPID'], '42')
            self.assertTrue(receipt['videoOnly'] and receipt['guardedPeersUnchanged'])
            self.assertFalse(receipt['installedRecipeVerified'] or receipt['releaseAdmissionVerified'])
            with self.assertRaisesRegex(ValueError, 'Single-use'): recycle.run(lambda _: True)
        self.assertEqual(fake.signals, [700]); self.assertEqual(fake.closed, [700])

    def test_bad_unit_restart_or_dropin_policy_fails_before_handle_or_signal(self):
        for change in ({'Restart': 'on-failure'}, {'RestartUSec': '3s'}, {'KillMode': 'process'},
                       {'DropInPaths': '/generated/foreign'}, {'FragmentPath': '/etc/systemd/system/other'},
                       {'ControlGroup': '/system.slice/other.service'}, {'InvocationID': '0' * 32},
                       {'MainPID': '01'}, {'ControlPID': '5'}, {'LoadState': 'not-found'}):
            fake = Fake(); fake.video.update(change)
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=fake): pass
            self.assertEqual(fake.handles, []); self.assertEqual(fake.signals, [])

    def test_exact_owned_dropin_needs_fresh_separate_graph_proof(self):
        fake=Fake();fake.video['DropInPaths']=R.OWNED_DROP;fake.new['DropInPaths']=R.OWNED_DROP
        for proof in (None,lambda:False,lambda:1):
            with self.assertRaises(ValueError),R.hold_video_recycle(admit=lambda:True,owned_graph=proof,_backend=fake):pass
            self.assertEqual(fake.signals,[])
        with R.hold_video_recycle(admit=lambda:True,owned_graph=lambda:True,_backend=fake) as cycle:
            self.assertTrue(cycle.run(lambda _:True)['ready'])
        self.assertEqual(fake.signals,[700])
    def test_owned_callback_never_admits_foreign_dropin_or_survives_proof_loss(self):
        fake=Fake();fake.video['DropInPaths']='/run/systemd/system/foreign.conf'
        with self.assertRaises(ValueError),R.hold_video_recycle(admit=lambda:True,owned_graph=lambda:True,_backend=fake):pass
        self.assertEqual(fake.signals,[])
        fake=Fake();fake.video['DropInPaths']=R.OWNED_DROP
        admitted=[True]
        with R.hold_video_recycle(admit=lambda:True,owned_graph=lambda:admitted[0],_backend=fake) as cycle:
            admitted[0]=False
            with self.assertRaises(ValueError):cycle.run(lambda _:True)
        self.assertEqual(fake.signals,[])
    def test_owned_readiness_has_bounded_startup_budget_without_changing_legacy(self):
        for owned in (False,True):
            fake=Fake()
            with R.hold_video_recycle(admit=lambda:True,owned_graph=(lambda:True) if owned else None,_backend=fake) as cycle:
                if owned:self.assertTrue(cycle.run(lambda _:fake.clock>=12)['ready'])
                else:
                    with self.assertRaises(TimeoutError):cycle.run(lambda _:fake.clock>=12)
            self.assertEqual(fake.signals,[700])
    def test_required_independent_admission_and_root_platform(self):
        for admit in (None, lambda: False, lambda: 1):
            fake = Fake()
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=admit, _backend=fake): pass
            self.assertEqual(fake.handles, [])
        with patch.object(R.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=Fake()): pass

    def test_generation_change_after_pidfd_open_closes_without_signalling(self):
        fake = Fake()
        fake.on_handle = lambda: fake.video.update(MainPID='43')
        with self.assertRaisesRegex(ValueError, 'Original'), R.hold_video_recycle(admit=lambda: True, _backend=fake): pass
        self.assertEqual(fake.signals, []); self.assertEqual(fake.closed, [700])

    def test_admission_loss_after_policy_change_stops_before_signal(self):
        fake = Fake(); allowed = True
        with R.hold_video_recycle(admit=lambda: allowed, _backend=fake) as recycle:
            allowed = False
            with self.assertRaisesRegex(ValueError, 'admission'): recycle.run(lambda _: True)
        self.assertEqual(fake.signals, []); self.assertEqual(fake.closed, [700])

    def test_unrelated_service_change_or_boot_change_aborts_without_retry(self):
        for change in ('peer', 'boot'):
            fake = Fake()
            def mutate():
                if change == 'peer': fake.peers[R.GUARDS[0]]['MainPID'] = '999'
                else: fake.boot_id = 'c0a62724-6d9b-43fc-8fbd-39a08057832c'
            fake.on_signal = mutate
            with self.assertRaisesRegex(ValueError, 'Guarded'), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
                recycle.run(lambda _: True)
            self.assertEqual(fake.signals, [700]); self.assertEqual(fake.closed, [700])

    def test_restart_storm_config_change_and_stop_are_not_admitted(self):
        for change in ({'NRestarts': '10'}, {'RestartUSec': '1s'}, {'ActiveState': 'deactivating', 'SubState': 'stop'}):
            fake = Fake()
            fake.on_signal = lambda change=change: fake.video.update(change)
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
                recycle.run(lambda _: True)
            self.assertEqual(fake.signals, [700])

    def test_empty_cgroup_is_allowed_only_while_systemd_waits_to_restart(self):
        fake = Fake()
        fake.on_signal = lambda: fake.video.update(ControlGroup='')
        with R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
            self.assertTrue(recycle.run(lambda _: True)['ready'])
        for phase in ('active', 'activating'):
            fake = Fake()
            def wrong():
                fake.video.update(ControlGroup='/system.slice/foreign.service', ActiveState=phase)
            fake.on_signal = wrong
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
                recycle.run(lambda _: True)
            self.assertEqual(fake.signals, [700])

    def test_fresh_pid_requires_new_invocation_and_monotonic_birth(self):
        for change in ({'InvocationID': 'a' * 32}, {'ExecMainStartTimestampMonotonic': '1000'},
                       {'NRestarts': '8'}, {'ControlGroup': '/system.slice/other.service'}):
            fake = Fake(); fake.new.update(change)
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
                recycle.run(lambda _: True)
            self.assertEqual(fake.signals, [700])

    def test_readiness_timeout_never_resends_signal_or_expires_healthy_service(self):
        fake = Fake()
        with self.assertRaises(TimeoutError), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
            recycle.run(lambda _: False)
        self.assertEqual(fake.signals, [700]); self.assertEqual(fake.video['MainPID'], '42')
        self.assertLess(fake.clock, 9.2)

    def test_readiness_mutation_or_expired_deadline_rejected(self):
        for change in ('pid', 'deadline'):
            fake = Fake()
            def ready(value):
                if change == 'pid': fake.video['MainPID'] = '44'
                else: fake.clock = 10
                return True
            with self.assertRaises(ValueError), R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
                recycle.run(ready)
            self.assertEqual(fake.signals, [700])

    def test_possibly_delivered_signal_is_single_use_and_closed(self):
        fake = Fake()
        def uncertain(): raise OSError('generated signal delivery uncertainty')
        fake.on_signal = uncertain
        with R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
            with self.assertRaises(OSError): recycle.run(lambda _: True)
            with self.assertRaises(ValueError): recycle.run(lambda _: True)
        self.assertEqual(fake.signals, [700]); self.assertEqual(fake.closed, [700])

    def test_context_cannot_be_inherited_by_another_process(self):
        fake = Fake()
        with R.hold_video_recycle(admit=lambda: True, _backend=fake) as recycle:
            with patch.object(R.os, 'getpid', return_value=999999):
                with self.assertRaises(ValueError): recycle.run(lambda _: True)
        self.assertEqual(fake.signals, [])

    def test_status_parser_rejects_unknown_keys_and_noncanonical_counters(self):
        for value in ({**unit(R.VIDEO), 'extra': 'generated'}, {**unit(R.VIDEO), 'NRestarts': '-1'},
                      {**unit(R.VIDEO), 'NRestarts': '01'}, {**unit(R.VIDEO), 'MainPID': True}):
            with self.assertRaises(ValueError): R.record(value, R.VIDEO)


if __name__ == '__main__': unittest.main()
