# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


S = load('readiness_supervision', HERE / 'machine-gpu-supervision.py')
BOOT = '12345678-1234-1234-1234-123456789abc'


class ReadinessBoundary(unittest.TestCase):
    def test_platform_root_and_admission_precede_observation_or_socket(self):
        self.assertTrue(callable(getattr(S, 'hold_watcher_readiness', None)))
        with ExitStack() as stack, mock.patch.object(S, 'unit', side_effect=AssertionError('no observation')):
            for platform, uid, admit in (('win32', 0, lambda: None), ('linux', 1000, lambda: None), ('linux', 0, None)):
                with mock.patch.object(S.sys, 'platform', platform), mock.patch.object(S.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        S.hold_watcher_readiness(stack, admit=admit)
            denied = mock.Mock(side_effect=ValueError('generated missing prerequisites'))
            with mock.patch.object(S.sys, 'platform', 'linux'), mock.patch.object(S.os, 'geteuid', return_value=0, create=True), \
                    mock.patch.dict(os.environ, {'NOTIFY_SOCKET': str(S.NOTIFY_SOCKET), 'INVOCATION_ID': 'a' * 32}):
                with self.assertRaises(ValueError):
                    S.hold_watcher_readiness(stack, admit=denied)
                denied.assert_called_once_with()

    def test_closed_starting_watcher_schema(self):
        self.assertTrue(callable(getattr(S, '_starting_watcher', None)))
        base = {'LoadState': 'loaded', 'ActiveState': 'activating', 'SubState': 'start', 'MainPID': '42',
                'ControlPID': '0', 'InvocationID': 'a' * 32, 'ControlGroup': '/system.slice/' + S.WATCHER}
        self.assertEqual(S._starting_watcher(base), base)
        for change in ({'ActiveState': 'active', 'SubState': 'running'}, {'SubState': 'start-pre'},
                       {'ControlPID': '9'}, {'MainPID': '042'}, {'InvocationID': '0' * 32},
                       {'ControlGroup': '/foreign'}, {'extra': 'foreign'}):
            with self.assertRaises(ValueError):
                S._starting_watcher({**base, **change})


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated Unix socket/pidfd fixtures required')
class ReadinessNative(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-readiness-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.endpoint = self.root / 'notify'
        self.receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(self.receiver.close)
        self.receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.receiver.bind(str(self.endpoint))
        self.receiver.settimeout(0.05)
        self.record = {'LoadState': 'loaded', 'ActiveState': 'activating', 'SubState': 'start', 'MainPID': str(os.getpid()),
                       'ControlPID': '0', 'InvocationID': 'a' * 32, 'ControlGroup': '/system.slice/' + S.WATCHER}
        self.state = {'boot': BOOT, 'delta': 0, 'deny': False}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(S, 'NOTIFY_SOCKET', self.endpoint).start()
        mock.patch.dict(os.environ, {'NOTIFY_SOCKET': str(self.endpoint), 'INVOCATION_ID': 'a' * 32}).start()

    def admit(self):
        if self.state['deny']:
            raise ValueError('Generated source/effective graph admission failure')

    def identify(self, pid, _group):
        return int(Path('/proc/' + str(pid) + '/stat').read_bytes().rpartition(b') ')[2].split()[19]) + self.state['delta']

    def hold(self, stack, module=S, admit=None):
        return module.hold_watcher_readiness(stack, admit=self.admit if admit is None else admit,
            observe=lambda name: dict(self.record) if name == module.WATCHER else None,
            identify=self.identify, current_boot=lambda: self.state['boot'])

    def packet(self):
        raw, ancillary, flags, _ = self.receiver.recvmsg(128, socket.CMSG_SPACE(struct.calcsize('3i')))
        self.assertEqual(raw, b'READY=1')
        self.assertEqual(flags, 0)
        credentials = [struct.unpack('3i', data) for level, kind, data in ancillary
                       if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
        self.assertEqual(credentials, [(os.getpid(), 0, 0)])

    def quiet(self):
        with self.assertRaises(TimeoutError):
            self.receiver.recv(128)

    def test_exact_one_packet_has_actual_main_process_credentials_and_fds_close(self):
        opened = []
        actual = S.os.pidfd_open
        def pidfd(pid, flags):
            fd = actual(pid, flags)
            opened.append(fd)
            return fd
        with ExitStack() as stack, mock.patch.object(S.os, 'pidfd_open', side_effect=pidfd):
            held = self.hold(stack)
            self.quiet()
            held['check']()
            policy = held['notify']()
            self.assertTrue(policy['readinessDatagramSent'])
            self.assertTrue(all(policy[key] is False for key in ('installedRecipeVerified', 'effectiveGraphVerified',
                'independentRecoveryArmed', 'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation')))
            self.assertNotIn(str(self.root), json.dumps(policy))
            self.packet()
            with self.assertRaises(ValueError):
                held['notify']()
            self.quiet()
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_foreign_environment_or_already_active_refuses_before_pidfd(self):
        for key, value in (('NOTIFY_SOCKET', '@foreign'), ('NOTIFY_SOCKET', '/tmp/foreign'),
                           ('INVOCATION_ID', 'b' * 32), ('INVOCATION_ID', '')):
            with mock.patch.dict(os.environ, {key: value}), ExitStack() as stack, \
                    mock.patch.object(S.os, 'pidfd_open', side_effect=AssertionError('no pidfd')):
                with self.assertRaises(ValueError):
                    self.hold(stack)
        self.record.update(ActiveState='active', SubState='running')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.quiet()

    def test_changed_generation_birth_boot_recipe_environment_or_caller_never_sends(self):
        for kind in ('invocation', 'birth', 'boot', 'recipe', 'environment', 'caller'):
            self.state.update(boot=BOOT, delta=0, deny=False)
            self.record['InvocationID'] = 'a' * 32
            with ExitStack() as stack:
                held = self.hold(stack)
                if kind == 'invocation':
                    self.record['InvocationID'] = 'b' * 32
                elif kind == 'birth':
                    self.state['delta'] = 1
                elif kind == 'boot':
                    self.state['boot'] = '87654321-1234-1234-1234-123456789abc'
                elif kind == 'recipe':
                    self.state['deny'] = True
                elif kind == 'environment':
                    with mock.patch.dict(os.environ, {'NOTIFY_SOCKET': '@foreign'}), self.assertRaises(ValueError):
                        held['notify']()
                    continue
                else:
                    with mock.patch.object(S.os, 'getpid', return_value=os.getpid() + 1), self.assertRaises(ValueError):
                        held['notify']()
                    continue
                with self.assertRaises(ValueError):
                    held['notify']()
            self.quiet()

    def test_socket_path_replacement_is_not_adopted(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            self.endpoint.unlink()
            other = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.addCleanup(other.close)
            other.bind(str(self.endpoint))
            with self.assertRaises(ValueError):
                held['notify']()
        self.quiet()

    def sender(self, callback):
        actual = S.socket.socket
        class Sender:
            def __init__(self, *args):
                self.raw = actual(*args)
            def __getattr__(self, key):
                return getattr(self.raw, key)
            def send(self, raw):
                count = self.raw.send(raw)
                callback()
                return count
        return mock.patch.object(S.socket, 'socket', Sender)

    def test_postsend_admission_failure_is_uncertain_and_cannot_retry(self):
        with ExitStack() as stack, self.sender(lambda: self.state.update(deny=True)):
            held = self.hold(stack)
            with self.assertRaises(S.WatcherReadinessUncertain):
                held['notify']()
            self.packet()
            self.state['deny'] = False
            with self.assertRaises(ValueError):
                held['notify']()
            self.quiet()

    def test_active_transition_only_after_send_and_never_regresses(self):
        with ExitStack() as stack, self.sender(lambda: self.record.update(ActiveState='active', SubState='running')):
            held = self.hold(stack)
            held['notify']()
            held['check']()
            self.packet()
            self.record.update(ActiveState='activating', SubState='start')
            with self.assertRaises(ValueError):
                held['check']()

    def test_normal_activation_between_postsend_observations_is_not_uncertainty(self):
        def observe(_name):
            value = dict(self.record)
            if self.state.get('transition'):
                self.record.update(ActiveState='active', SubState='running')
            return value
        with ExitStack() as stack, self.sender(lambda: self.state.update(transition=True)):
            held = S.hold_watcher_readiness(stack, admit=self.admit, observe=observe,
                identify=self.identify, current_boot=lambda: self.state['boot'])
            held['notify']()
            self.packet()
            held['check']()

    def test_current_pinned_bundle_loads_the_actual_readiness_callback(self):
        package = self.root / 'package'
        subprocess.run([shutil.which('node'), '-e', 'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(HERE / 'machine-gpu-bundle.js'), str(package)], check=True, capture_output=True, timeout=15)
        P = load('readiness_held_package', HERE / 'machine-gpu-package.py')
        with ExitStack() as stack:
            source = P.hold_source_package(stack, package)
            entry = types.ModuleType('readiness_pinned_entry')
            entry.__file__ = str(package / 'machine-gpu-entry.py')
            exec(compile(source['read']('machine-gpu-entry.py'), entry.__file__, 'exec'), entry.__dict__)
            sources = entry.hold_runtime_sources(stack, bootstrap=package / 'machine-gpu-bootstrap.py', root=package / 'machine-gpu-runtime')
            module = sources['load']('supervision')
            module.NOTIFY_SOCKET = self.endpoint  # Generated transport endpoint; no live systemd notification.
            def admit():
                source['check']()
                sources['check']()
                self.admit()
            held = self.hold(stack, module, admit)
            lifecycle = sources['load']('lifecycle')
            expected = dict.fromkeys(lifecycle.BINDINGS[:3], 'a' * 64)
            expected.update(dataDevice=package.stat().st_dev, dataInode=package.stat().st_ino)
            status = {'kind': 'protected-machine-gpu-lifecycle', 'version': 1, 'stage': 'prepared',
                      'revision': 0, 'boot': BOOT, 'since': 0, **expected}
            def forbidden(*_args):
                raise AssertionError('No worker/guest/recovery action during readiness admission')
            watcher = lifecycle.Watcher(sources, expected, admit=admit, read_status=lambda: dict(status),
                native=forbidden, stop_worker=forbidden, worker_stopped=forbidden, stop_guest=forbidden,
                guest_stopped=forbidden, recover=forbidden, mark_restored=forbidden, notify=held['notify'],
                current_boot=lambda: BOOT, clock=lambda: 1, sleep=forbidden)
            self.assertTrue(watcher.arm())
            self.packet()
            sources['check']()
            self.assertFalse(sources['policy']['effectiveGraphVerified'])


if __name__ == '__main__':
    unittest.main()
