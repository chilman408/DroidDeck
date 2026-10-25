# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('quiescence_tested', Path(__file__).with_name('update-quiescence.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
TOKEN, DIGEST, BOOT = '012345abcdef', 'a' * 64, 'b' * 36
QUIET = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
TEMPLATE, GUARD_TEMPLATE = b'original generated watcher\n', b'enrolled generated guard\n'
EVIDENCE = {'kind': 'android-gpu-native-health', 'nativeGpuVerified': True,
            'containerPid': 700, 'surfaceFlingerPid': 40, 'containerIdentity': '4:987:123', 'androidIdentity': '30:400/40:500'}


class Fixture:
    def __init__(self):
        self.events, self.locked, self.on_stop = [], False, lambda: None
        self.before = copy.deepcopy(EVIDENCE)
        self.g = SimpleNamespace(WATCHER='droiddeck-fresh-gpu-watch@' + TOKEN + '.service',
            WORKER='droiddeck-fresh-gpu-worker@' + TOKEN + '.service', STATE=Path('/var/lib/droiddeck/gpu-transactions') / TOKEN,
            ROOT=Path('/root/generated-source'), boot_id=lambda: BOOT, sources=lambda: None)
        self.units = {self.g.WATCHER: {'ActiveState': 'active', 'MainPID': '100', 'ControlPID': '0'},
                      self.g.WORKER: dict(QUIET), D.worker(DIGEST): dict(QUIET),
                      D.GUARD: {'ActiveState': 'active', 'MainPID': str(os.getpid()), 'ControlPID': '0'}}
        self.g.unit = lambda name: dict(self.units[name])
        self.g.require_quiet = self.quiet
        def protected_read(path, mode, *_args):
            expected = 0o644 if path.name == D.GUARD else 0o600
            if mode != expected: raise ValueError('Exact original guard/watcher mode required')
            return GUARD_TEMPLATE if path.name == D.GUARD else TEMPLATE
        self.g.protected_read = protected_read
        self.g.command = self.command
        self.state = {'stage': 'ready', 'boot': BOOT, 'since': 1}
        self.active = SimpleNamespace(admission={'transaction': TOKEN}, metadata=lambda: None,
            store=SimpleNamespace(status=lambda: dict(self.state), lock=self.lock))
        self.journal = SimpleNamespace(**{name: self.record(name) for name in ('intent', 'granted', 'return_intent', 'returned')})

    def record(self, name):
        def run(*_args): self.events.append(name)
        return run

    @contextmanager
    def lock(self):
        if self.locked: raise BlockingIOError('Original recovery already owns lock')
        self.locked = True; self.events.append('lock')
        try: yield
        finally: self.locked = False; self.events.append('unlock')

    def quiet(self, state):
        if state['ActiveState'] not in ('inactive', 'failed') or state['MainPID'] != '0' or state['ControlPID'] != '0':
            raise ValueError('Not quiescent')

    def command(self, args, **_kwargs):
        if args[0] == '/usr/bin/node':
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.before).encode())
        if args[1] == 'show':
            name = args[2]
            fragment = '/etc/systemd/system/' + (D.GUARD if name == D.GUARD else 'droiddeck-fresh-gpu-watch@.service')
            return SimpleNamespace(returncode=0, stdout=('FragmentPath=' + fragment + '\nDropInPaths=\n').encode())
        if args[1] == 'stop':
            self.events.append('stop:' + args[2])
            self.on_stop()
            self.units[args[2]] = dict(QUIET)
        elif args[1] == 'start':
            if self.locked: raise AssertionError('Cannot restart original owner while its mutation lock is held')
            self.events.append('start:' + args[-1])
            self.units[args[-1]] = {'ActiveState': 'active', 'MainPID': '101', 'ControlPID': '0'}
        else: raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout=b'')

    def fence(self):
        return D.Fence(self.g, self.active, self.journal, DIGEST, hashlib.sha256(GUARD_TEMPLATE).hexdigest())


class Policy(unittest.TestCase):
    def setUp(self):
        p = patch.object(D, 'WATCH_PIN', hashlib.sha256(TEMPLATE).hexdigest())
        p.start(); self.addCleanup(p.stop)
        self.f = Fixture()

    def test_fixed_worker_scope_and_no_live_cli(self):
        for value in ('', 'x.service', '../escape', 'A' * 64, 'a' * 65, True):
            with self.assertRaises(ValueError): D.worker(value)
        result = subprocess.run([sys.executable, '-I', '-B', D.__file__, 'apply'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)

    def test_intent_precedes_stop_independent_guard_and_shared_lock_remain_held(self):
        fence = self.f.fence().acquire()
        self.assertTrue(self.f.locked)
        self.assertEqual(self.f.events, ['lock', 'intent', 'stop:' + self.f.g.WATCHER, 'granted'])
        fence.assert_stable()
        fence.return_owner(lambda: None)
        self.assertEqual(self.f.events[-4:], ['return_intent', 'unlock', 'start:' + self.f.g.WATCHER, 'returned'])
        self.assertFalse(self.f.locked)
        with self.assertRaises(ValueError): fence.acquire()

    def test_guard_absent_or_not_main_refuses_before_any_old_service_stop(self):
        for changes in ({'ActiveState': 'inactive'}, {'MainPID': '1'}, {'ControlPID': '22'}):
            self.f.units[D.GUARD].update(changes)
            with self.assertRaises(ValueError): self.f.fence().acquire()
            self.assertEqual(self.f.events, [])
            self.f = Fixture()

    def test_foreign_template_or_metadata_or_scope_refuses_before_handoff(self):
        self.f.g.protected_read = lambda *_args: b'foreign'
        with self.assertRaises(ValueError): self.f.fence().acquire()
        self.assertEqual(self.f.events, [])
        self.f = Fixture(); self.f.g.STATE = Path('/foreign')
        with self.assertRaises(ValueError): self.f.fence().acquire()
        self.assertEqual(self.f.events, [])

    def test_active_worker_old_owner_missing_and_stale_gpu_epoch_refuse(self):
        for change in ('worker', 'owner', 'boot', 'stage'):
            self.f = Fixture()
            if change == 'worker': self.f.units[D.worker(DIGEST)]['MainPID'] = '200'
            elif change == 'owner': self.f.units[self.f.g.WATCHER] = dict(QUIET)
            elif change == 'boot': self.f.state['boot'] = 'old'
            else: self.f.state['stage'] = 'restored'
            with self.assertRaises(ValueError): self.f.fence().acquire()
            self.assertFalse(any(value.startswith('stop:') for value in self.f.events))
            self.assertFalse(self.f.locked)

    def test_recovery_already_holding_lock_wins_and_no_stop_or_intent_occurs(self):
        self.f.locked = True
        with self.assertRaises(BlockingIOError): self.f.fence().acquire()
        self.assertEqual(self.f.events, [])

    def test_native_generation_change_after_stop_rearms_only_unchanged_original_code(self):
        self.f.on_stop = lambda: self.f.before.update(androidIdentity='30:400/40:501')
        with self.assertRaises(ValueError): self.f.fence().acquire()
        self.assertNotIn('granted', self.f.events)
        self.assertEqual(self.f.events[-2:], ['unlock', 'start:' + self.f.g.WATCHER])

    def test_lost_grant_persistence_rearms_without_granting_any_code_authority(self):
        def refuse(): raise OSError('generated durable journal failure')
        self.f.journal.granted = refuse
        with self.assertRaises(OSError): self.f.fence().acquire()
        self.assertEqual(self.f.events[-2:], ['unlock', 'start:' + self.f.g.WATCHER])

    def test_unknown_code_return_cannot_unlock_or_rearm_original_watcher(self):
        fence = self.f.fence().acquire()
        for verifier in (lambda: False, lambda: True, lambda: {}):
            with self.assertRaises(ValueError): fence.return_owner(verifier)
            self.assertTrue(self.f.locked)
            self.assertFalse(any(value.startswith('start:') for value in self.f.events))
        fence.return_owner(lambda: None)

    def test_tracked_worker_stops_before_source_checks_and_code_rollback(self):
        fence = self.f.fence().acquire()
        self.f.units[D.worker(DIGEST)] = {'ActiveState': 'active', 'MainPID': '200', 'ControlPID': '0'}
        with self.assertRaises(ValueError): fence.return_owner(lambda: None)
        self.f.g.sources = lambda: (_ for _ in ()).throw(ValueError('changed source'))
        with self.assertRaises(ValueError): fence.stop_mutator()
        self.assertEqual(self.f.events[-1], 'stop:' + D.worker(DIGEST))
        self.f.g.sources = lambda: None
        fence.return_owner(lambda: None)

    def test_same_pid_but_changed_birth_is_not_same_native_generation(self):
        fence = self.f.fence().acquire()
        self.f.before['containerIdentity'] = '4:987:124'
        with self.assertRaises(ValueError): fence.assert_stable()
        # Code verification, not a false healthy flag, controls safe return.
        fence.return_owner(lambda: None)

    def test_invalid_native_metadata_never_grants_a_fence(self):
        for changes in ({'nativeGpuVerified': False}, {'containerIdentity': 'unbound'},
                        {'androidIdentity': '30 40'}, {'containerPid': True}):
            self.f = Fixture(); self.f.before.update(changes)
            with self.assertRaises(ValueError): self.f.fence().acquire()
            self.assertNotIn('granted', self.f.events)

    def test_boolean_journal_flags_are_not_durable_ownership(self):
        self.f.journal.intent = lambda *_args: False
        with self.assertRaises(ValueError): self.f.fence().acquire()
        self.assertFalse(any(value.startswith('stop:') for value in self.f.events))
        self.f = Fixture(); self.f.journal.granted = lambda: True
        with self.assertRaises(ValueError): self.f.fence().acquire()
        self.assertEqual(self.f.events[-2:], ['unlock', 'start:' + self.f.g.WATCHER])


@unittest.skipUnless(sys.platform == 'linux', 'Native shared flock pending-recovery fixture')
class Kernel(unittest.TestCase):
    def test_prior_recovery_decision_cannot_mutate_behind_the_original_shared_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory(prefix='droiddeck-quiescence-lock-') as directory:
            lock = Path(directory) / 'lock'
            lock.write_bytes(b'')
            @contextmanager
            def held():
                with lock.open('r+b') as file:
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try: yield
                    finally: fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            code = '''import fcntl,sys
print('decided',flush=True)
sys.stdin.readline()
with open(sys.argv[1],'r+b') as file:
    try:fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print('fenced',flush=True)
    else:print('UNSAFE',flush=True)
'''
            with subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(lock)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
                self.assertEqual(child.stdout.readline().strip(), 'decided')
                with held():
                    child.stdin.write('go\n'); child.stdin.flush()
                    self.assertEqual(child.stdout.readline().strip(), 'fenced')
                child.wait(timeout=10)
                self.assertEqual(child.returncode, 0)


if __name__ == '__main__':
    unittest.main()
