# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('machine_gpu_supervision', Path(__file__).with_name('machine-gpu-supervision.py'))
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
BOOT = '12345678-1234-1234-1234-123456789abc'


def record(name, pid, invocation='a' * 32):
    return {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running', 'MainPID': str(pid),
            'ControlPID': '0', 'InvocationID': invocation, 'ControlGroup': '/system.slice/' + name}


class SupervisionBoundaries(unittest.TestCase):
    def test_root_linux_and_recipe_admission_precede_observation(self):
        with mock.patch.object(S, 'unit', side_effect=AssertionError('must not observe')), ExitStack() as stack:
            for platform, uid, admit in (('win32', 0, lambda: None), ('linux', 1000, lambda: None), ('linux', 0, None)):
                with mock.patch.object(S.sys, 'platform', platform), mock.patch.object(S.os, 'geteuid', return_value=uid, create=True), self.assertRaises(ValueError):
                    S.hold_worker_supervision(stack, admit=admit)
            def deny():
                raise ValueError('generated missing recipe admission')
            with mock.patch.object(S.sys, 'platform', 'linux'), mock.patch.object(S.os, 'geteuid', return_value=0, create=True), self.assertRaises(ValueError):
                S.hold_worker_supervision(stack, admit=deny)

    def test_closed_live_peer_schema_and_canonical_generation(self):
        base = record(S.WATCHER, 42)
        self.assertEqual(S._peer(base, S.WATCHER), base)
        for change in ({'LoadState': 'not-found'}, {'ActiveState': 'activating'}, {'ActiveState': 'failed'},
                       {'SubState': 'exited'}, {'ControlPID': '9'}, {'MainPID': '042'}, {'MainPID': '1'},
                       {'MainPID': str(2**31)}, {'MainPID': True}, {'InvocationID': '0' * 32},
                       {'InvocationID': 'A' * 32}, {'ControlGroup': '/system.slice/foreign.service'}, {'extra': 'generated'}):
            with self.assertRaises(ValueError):
                S._peer({**base, **change}, S.WATCHER)

    def test_fixed_readonly_command_and_closed_output(self):
        raw = ''.join(k + '=' + v + '\n' for k, v in record(S.WATCHER, 42).items()).encode()
        with mock.patch.object(S.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=raw)) as run:
            self.assertEqual(S.unit(S.WATCHER), record(S.WATCHER, 42))
            args, kwargs = run.call_args
            self.assertEqual(args[0][:3], ['/usr/bin/systemctl', 'show', S.WATCHER])
            self.assertEqual(kwargs['timeout'], 5)
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertNotIn('shell', kwargs)
            self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        for data in (raw + b'ControlPID=0\n', raw.replace(b'ControlPID=0', b'Extra=0'), b'x' * 4097, b'\xff', b'broken'):
            with mock.patch.object(S.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=data)), self.assertRaises(ValueError):
                S.unit(S.WATCHER)
        with mock.patch.object(S.subprocess, 'run', side_effect=AssertionError('must not invoke')), self.assertRaises(ValueError):
            S.unit('droiddeck-gpu-recovery.service')

    def test_failed_command_or_timeout_has_no_fallback(self):
        with mock.patch.object(S.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout=b'')), self.assertRaises(ValueError):
            S.unit(S.WATCHER)
        with mock.patch.object(S.subprocess, 'run', side_effect=subprocess.TimeoutExpired('generated', 5)), self.assertRaises(subprocess.TimeoutExpired):
            S.unit(S.WATCHER)

    def test_boot_identity_is_canonical(self):
        for value in (BOOT + '\n', BOOT, 'foreign\n', BOOT.upper() + '\n'):
            with mock.patch.object(S.Path, 'read_text', return_value=value):
                if value == BOOT + '\n':
                    self.assertEqual(S.boot(), BOOT)
                else:
                    with self.assertRaises(ValueError):
                        S.boot()

    def test_process_arguments_are_closed_before_proc_reads(self):
        with mock.patch.object(S.os, 'open', side_effect=AssertionError('must not open')):
            for pid, group in ((True, '/system.slice/' + S.WATCHER), (1, '/system.slice/' + S.WATCHER),
                               (2**31, '/system.slice/' + S.WATCHER), (42, '/foreign')):
                with self.assertRaises(ValueError):
                    S.process(pid, group)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated supervision/process fixtures required')
class PeerLifetimes(unittest.TestCase):
    def peers(self):
        child = subprocess.Popen(['/usr/bin/python3', '-I', '-B', '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def cleanup():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.addCleanup(cleanup)
        records = {S.WATCHER: record(S.WATCHER, child.pid), S.WORKER: record(S.WORKER, os.getpid(), 'b' * 32)}
        def birth(pid, _group):
            return int(Path('/proc/' + str(pid) + '/stat').read_bytes().rpartition(b') ')[2].split()[19])
        kwargs = {'admit': lambda: None, 'observe': lambda name: dict(records[name]),
                  'identify': birth, 'current_boot': lambda: BOOT}
        return child, records, kwargs

    def test_original_live_peers_check_and_descriptors_close(self):
        _, _, kwargs = self.peers()
        fds = []
        pidfd_open = S.os.pidfd_open
        def opened(pid, flags):
            fd = pidfd_open(pid, flags)
            fds.append(fd)
            return fd
        with ExitStack() as stack, mock.patch.object(S.os, 'pidfd_open', side_effect=opened):
            held = S.hold_worker_supervision(stack, **kwargs)
            held['check']()
            held['check']()
            for key in ('installedRecipeVerified', 'effectiveGraphVerified', 'independentRecoveryArmed',
                        'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
                self.assertFalse(held[key])
        self.assertEqual(len(fds), 2)
        for fd in fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_dead_watcher_pidfd_refuses_even_if_observation_is_replayed(self):
        child, _, kwargs = self.peers()
        with ExitStack() as stack:
            held = S.hold_worker_supervision(stack, **kwargs)
            child.terminate()
            child.wait(timeout=5)
            with mock.patch.object(S, 'process', side_effect=AssertionError('no private reads')), self.assertRaises(ValueError):
                held['check']()

    def test_generation_changes_are_not_readopted(self):
        for kind in ('invocation', 'pid', 'control', 'inactive', 'birth', 'boot', 'recipe', 'caller'):
            _, records, kwargs = self.peers()
            values = {'boot': BOOT, 'birthDelta': 0, 'deny': False}
            original = kwargs['identify']
            kwargs['identify'] = lambda pid, group: original(pid, group) + values['birthDelta']
            kwargs['current_boot'] = lambda: values['boot']
            def admit():
                if values['deny']:
                    raise ValueError('generated recipe changed')
            kwargs['admit'] = admit
            with ExitStack() as stack:
                held = S.hold_worker_supervision(stack, **kwargs)
                if kind in ('invocation', 'pid', 'control', 'inactive'):
                    key, value = {'invocation': ('InvocationID', 'c' * 32), 'pid': ('MainPID', str(os.getpid())),
                                  'control': ('ControlPID', '9'), 'inactive': ('ActiveState', 'inactive')}[kind]
                    records[S.WATCHER][key] = value
                elif kind == 'birth':
                    values['birthDelta'] = 1
                elif kind == 'boot':
                    values['boot'] = '87654321-1234-1234-1234-123456789abc'
                elif kind == 'recipe':
                    values['deny'] = True
                else:
                    with mock.patch.object(S.os, 'getpid', return_value=os.getpid() + 1), self.assertRaises(ValueError):
                        held['check']()
                    continue
                with self.assertRaises(ValueError):
                    held['check']()

    def test_invalid_process_birth_or_boot_never_opens_pidfds(self):
        for key, value in (('identify', lambda *_: True), ('identify', lambda *_: 0),
                           ('current_boot', lambda: True), ('current_boot', lambda: BOOT + '\n')):
            _, _, kwargs = self.peers()
            kwargs[key] = value
            with ExitStack() as stack, mock.patch.object(S.os, 'pidfd_open', side_effect=AssertionError('must not open')), self.assertRaises(ValueError):
                S.hold_worker_supervision(stack, **kwargs)

    def test_second_pidfd_open_failure_closes_first_on_unwind(self):
        _, _, kwargs = self.peers()
        opened = []
        pidfd_open = S.os.pidfd_open
        def fail(pid, flags):
            if opened:
                raise OSError('generated second peer disappeared')
            fd = pidfd_open(pid, flags)
            opened.append(fd)
            return fd
        with self.assertRaises(OSError), ExitStack() as stack, mock.patch.object(S.os, 'pidfd_open', side_effect=fail):
            S.hold_worker_supervision(stack, **kwargs)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_snapshot_race_and_foreign_worker_refuse_before_pidfd_open(self):
        for foreign in (True, False):
            child, records, kwargs = self.peers()
            if foreign:
                records[S.WORKER]['MainPID'] = str(child.pid)
            else:
                calls = 0
                def observe(name):
                    nonlocal calls
                    calls += 1
                    return {**records[name], 'InvocationID': 'c' * 32} if calls > 2 else dict(records[name])
                kwargs['observe'] = observe
            with ExitStack() as stack, mock.patch.object(S.os, 'pidfd_open', side_effect=AssertionError('must not open')), self.assertRaises(ValueError):
                S.hold_worker_supervision(stack, **kwargs)

    def test_proc_parser_root_credentials_cgroup_state_and_birth(self):
        pid = os.getpid()
        group = '/system.slice/' + S.WORKER
        stat_raw = str(pid).encode() + b' (generated (name)) S ' + b'0 ' * 18 + b'123\n'
        good = {'stat': stat_raw, 'status': b'Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n', 'cgroup': ('0::' + group + '\n').encode()}
        with mock.patch.object(S, '_read', side_effect=lambda _fd, name, _max: good[name]):
            self.assertEqual(S.process(pid, group), 123)
        for key, raw in (('stat', stat_raw.replace(b') S ', b') Z ')), ('stat', b'bad'),
                         ('stat', stat_raw.replace(b'123', b'0')), ('status', good['status'].replace(b'0\t0\t0\t0', b'0\t1000\t0\t0')),
                         ('status', good['status'] + b'Uid:\t0\t0\t0\t0\n'), ('cgroup', b'0::/foreign\n')):
            changed = {**good, key: raw}
            with mock.patch.object(S, '_read', side_effect=lambda _fd, name, _max: changed[name]), self.assertRaises(ValueError):
                S.process(pid, group)
        calls = 0
        def changed_birth(_fd, name, _maximum):
            nonlocal calls
            if name == 'stat':
                calls += 1
                return stat_raw if calls == 1 else stat_raw.replace(b'123', b'124')
            return good[name]
        with mock.patch.object(S, '_read', side_effect=changed_birth), self.assertRaises(ValueError):
            S.process(pid, group)

    def recovery(self):
        spec = importlib.util.spec_from_file_location('supervision_recovery_fixtures', Path(__file__).with_name('machine_gpu_recovery_run_test.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        f = module.RecoveryRun()
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f, module.P

    def test_real_controller_uses_guard_through_five_actions_and_saves(self):
        f, _ = self.recovery()
        _, _, kwargs = self.peers()
        with ExitStack() as stack:
            held = S.hold_worker_supervision(stack, **kwargs)
            result = f.run_recovery(supervised=held['check'])
        self.assertEqual((result['actionsPerformed'], result['progressCheckpoints']), (5, 5))
        self.assertFalse(result['independentRecoveryArmed'])
        self.assertFalse(result['servicesActivated'])

    def test_dead_peer_before_controller_changes_no_files(self):
        f, _ = self.recovery()
        child, _, kwargs = self.peers()
        with ExitStack() as stack:
            held = S.hold_worker_supervision(stack, **kwargs)
            before = f.status_fixture.snapshot()
            child.terminate()
            child.wait(timeout=5)
            with self.assertRaises(ValueError):
                f.run_recovery(supervised=held['check'])
            self.assertEqual(f.status_fixture.snapshot(), before)

    def test_peer_death_after_first_owned_action_is_uncertain_without_next_action(self):
        f, p = self.recovery()
        child, _, kwargs = self.peers()
        rename = p.rename_exclusive
        actions = []
        def terminated(*args, **kw):
            rename(*args, **kw)
            actions.append(args[1:])
            child.terminate()
            child.wait(timeout=5)
        with ExitStack() as stack:
            held = S.hold_worker_supervision(stack, **kwargs)
            with mock.patch.object(p, 'rename_exclusive', side_effect=terminated), self.assertRaises(p.PromotionUncertain):
                f.run_recovery(supervised=held['check'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(json.loads(f.journal.record.read_bytes())['sequence'], 0)
        self.assertTrue(f.status_fixture.status()['checkpointRequired'])


if __name__ == '__main__':
    unittest.main()
