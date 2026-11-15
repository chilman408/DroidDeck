# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value

ROOT = Path(__file__).parent
P = load('policy', ROOT / 'capture-policy-transaction.py')
R = load('recycle', ROOT / 'capture-video-recycle.py')
C = load('controller', ROOT / 'capture-policy-controller.py')
T = load('recycle_tests', ROOT / 'capture_video_recycle_test.py')
NATIVE = sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0
BINDING = {'sourceSha256': 'a' * 64, 'configSha256': 'b' * 64, 'releaseSha256': 'c' * 64}


class InputTests(unittest.TestCase):
    def test_boolean_or_partial_binding_is_not_an_admission(self):
        for value in (True, False, None, {}, {'sourceSha256': 'a' * 64}, {**BINDING, 'extra': 'private'}, {**BINDING, 'releaseSha256': 'generated-private'}):
            with self.assertRaises(ValueError): C.binding(value)
        self.assertEqual(C.binding(BINDING), BINDING)


@unittest.skipUnless(NATIVE, 'Root Linux generated file/controller flock tests')
class NativeTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(prefix='generated-policy-controller-'); self.addCleanup(directory.cleanup)
        root = Path(directory.name); root.chmod(0o700)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); self.addCleanup(os.close, fd)
        backend = T.Fake()
        # Each terminate creates another uniquely identified synthetic generation.
        def terminate(handle):
            backend.signals.append(handle); backend.sent = True
            pid = int(backend.video['MainPID']) + 1
            backend.video = T.unit(R.VIDEO, pid, int(backend.video['NRestarts']) + 1, format(pid, '032x'), int(backend.video['ExecMainStartTimestampMonotonic']) + 1000)
            backend.worker_mode = 'cpu' if not (root / P.POLICY).exists() else P.parse_policy((root / P.POLICY).read_bytes())
        backend.terminate = terminate; backend.worker_mode = 'cpu'
        backend.handle = lambda record: int(record['MainPID'])
        backend.exited = lambda handle: int(backend.video['MainPID']) != handle
        def ready(mode, record): return mode == backend.worker_mode and backend.video == record
        store = P.PolicyTransaction(fd)
        return root, fd, store, backend, ready

    def hold(self, store, backend, ready, **kwargs):
        return C.hold_controller(P, R, store, admit=lambda: dict(BINDING), ready=ready, _backend=backend, **kwargs)

    def test_change_accept_recover_no_expiry_rollback_then_retire(self):
        root, fd, store, backend, ready = self.fixture()
        with self.hold(store, backend, ready) as controller:
            result = controller.change('gpu'); self.assertEqual(result['phase'], 'accepted')
            self.assertEqual(backend.worker_mode, 'gpu'); self.assertEqual(len(backend.signals), 1)
            self.assertEqual(controller.recover()['phase'], 'accepted'); self.assertEqual(len(backend.signals), 1)
            result = controller.rollback(); self.assertEqual(result['phase'], 'restored'); self.assertEqual(backend.worker_mode, 'cpu')
            self.assertFalse(os.path.lexists(root / P.POLICY)); self.assertEqual(len(backend.signals), 2)
            self.assertEqual(controller.recover()['phase'], 'restored'); self.assertEqual(len(backend.signals), 2)
            self.assertEqual(controller.retire()['outcome'], 'restored')
            self.assertFalse(os.path.lexists(root / C.OPERATION)); self.assertFalse(os.path.lexists(root / P.JOURNAL))
            self.assertEqual(controller.recover(), {'phase': 'no-operation'})
            with self.assertRaises(ValueError): controller.rollback()

    def test_repeated_accepted_changes_keep_history_and_new_prior_mode(self):
        root, fd, store, backend, ready = self.fixture()
        with self.hold(store, backend, ready) as controller:
            for mode in ('gpu', 'cpu', 'gpu'):
                controller.change(mode); self.assertEqual(backend.worker_mode, mode)
                self.assertEqual(controller.retire()['outcome'], 'accepted')
            self.assertEqual(len(list(root.glob('.capture-policy-operation-history-*'))), 3)
            self.assertEqual(len(list(root.glob('.capture-policy-history-*'))), 3)

    def test_rejected_readiness_restores_policy_and_worker_before_reporting(self):
        root, fd, store, backend, ready = self.fixture()
        def reject(mode, current):
            if mode == 'gpu': raise ValueError('generated media rejection')
            return ready(mode, current)
        with self.hold(store, backend, reject) as controller:
            with self.assertRaises(C.ChangeRejected): controller.change('gpu')
            self.assertEqual(backend.worker_mode, 'cpu'); self.assertEqual(len(backend.signals), 2)
            self.assertEqual(json.loads((root / C.OPERATION).read_bytes())['phase'], 'restored')
            controller.retire()

    def test_lost_admission_preserves_pending_obligation_and_refuses_signal(self):
        root, fd, store, backend, ready = self.fixture(); current = dict(BINDING)
        def event(name):
            if name == 'operation-durable-changing': current['releaseSha256'] = 'd' * 64
        with C.hold_controller(P, R, store, admit=lambda: current, ready=ready, _backend=backend, checkpoint=event) as controller:
            with self.assertRaises(C.RecoveryRequired): controller.change('gpu')
        self.assertTrue(os.path.lexists(root / C.OPERATION)); self.assertEqual(backend.signals, [])
        with self.hold(store, backend, ready) as controller:
            self.assertEqual(controller.recover()['phase'], 'restored'); self.assertEqual(backend.signals, [])
            controller.retire()

    def test_controller_lock_serializes_independent_opens_and_rejects_reentry(self):
        root, fd, store, backend, ready = self.fixture()
        other = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.hold(store, backend, ready) as controller:
                with self.assertRaises(BlockingIOError), self.hold(P.PolicyTransaction(other), backend, ready): pass
                def reenter(mode, record): controller.recover(); return True
                controller.ready = reenter
                with self.assertRaises(C.RecoveryRequired): controller.change('gpu')
        finally: os.close(other)
        with self.hold(store, backend, ready) as controller: controller.recover(); controller.retire()

    def test_changed_peer_boot_lock_or_foreign_policy_are_preserved(self):
        for change in ('peer', 'boot', 'lock', 'policy'):
            root, fd, store, backend, ready = self.fixture()
            with self.hold(store, backend, ready) as controller:
                controller.change('gpu')
                if change == 'peer': backend.peers[R.GUARDS[0]]['MainPID'] = '999'
                if change == 'boot': backend.boot_id = 'c0a62724-6d9b-43fc-8fbd-39a08057832c'
                if change == 'lock': (root / C.LOCK).rename(root / 'generated-old-lock'); (root / C.LOCK).touch(mode=0o600)
                if change == 'policy': (root / P.POLICY).write_bytes(P.policy_bytes('cpu'))
                before = (root / C.OPERATION).read_bytes()
                with self.assertRaises(ValueError): controller.recover()
                self.assertEqual((root / C.OPERATION).read_bytes(), before)
                self.assertEqual(len(backend.signals), 1)

    def test_unbound_policy_journal_is_not_adopted(self):
        root, fd, store, backend, ready = self.fixture(); store.begin('gpu')
        with self.hold(store, backend, ready) as controller:
            with self.assertRaisesRegex(ValueError, 'Unbound'): controller.change('cpu')
            with self.assertRaisesRegex(ValueError, 'Unbound'): controller.recover()
        self.assertFalse(os.path.lexists(root / C.OPERATION)); self.assertEqual(backend.signals, [])

    def test_closed_record_never_exposes_malformed_bytes(self):
        root, fd, store, backend, ready = self.fixture()
        with self.hold(store, backend, ready) as controller:
            controller.change('gpu'); value = json.loads((root / C.OPERATION).read_bytes())
            for raw in (b'generated-private-sentinel', P.encode({**value, 'extra': 'private'}), P.encode({**value, 'binding': True}), P.encode({**value, 'phase': []})):
                with self.assertRaisesRegex(ValueError, '^Invalid capture operation record$'): controller._record(raw)

    def test_policy_token_and_expected_prior_are_checked_before_publication(self):
        root, fd, store, backend, ready = self.fixture()
        with self.assertRaises(ValueError): store.begin('gpu', token='../generated')
        with self.assertRaises(ValueError): store.begin('gpu', expected_before={'unsupported': True})
        (root / P.POLICY).write_bytes(P.policy_bytes('cpu')); (root / P.POLICY).chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'changed before'): store.begin('gpu', expected_before=None)
        self.assertFalse(os.path.lexists(root / P.JOURNAL))


    def test_operation_archive_collision_and_foreign_lock_never_overwritten(self):
        root, fd, store, backend, ready = self.fixture()
        with self.hold(store, backend, ready) as controller:
            result = controller.change('gpu')
            foreign = root / ('.capture-policy-operation-history-' + result['token'] + '.json')
            foreign.write_bytes(b'generated-private-receipt'); foreign.chmod(0o600)
            with self.assertRaises(ValueError): controller.retire()
            self.assertEqual(foreign.read_bytes(), b'generated-private-receipt')
            self.assertTrue(os.path.lexists(root / C.OPERATION)); self.assertEqual(len(backend.signals), 1)
        root, fd, store, backend, ready = self.fixture()
        (root / C.LOCK).symlink_to('generated-missing')
        with self.assertRaises(OSError), self.hold(store, backend, ready): pass
        self.assertEqual(os.readlink(root / C.LOCK), 'generated-missing')

    def test_inherited_controller_context_is_not_authority(self):
        root, fd, store, backend, ready = self.fixture()
        with self.hold(store, backend, ready) as controller:
            with patch.object(C.os, 'getpid', return_value=999999):
                with self.assertRaises(ValueError): controller.change('gpu')
            self.assertEqual(backend.signals, [])

    def test_process_death_obligations_survive_independent_descriptor_reopen(self):
        counts = {}
        for action in ('change', 'rollback', 'retire'):
            root, fd, store, backend, ready = self.fixture()
            if action != 'change':
                with self.hold(store, backend, ready) as controller: controller.change('gpu')
            events = []
            with self.hold(store, backend, ready, checkpoint=events.append) as controller:
                getattr(controller, action)(*(['gpu'] if action == 'change' else []))
            counts[action] = len(events)
            for boundary, event_name in enumerate(events, 1):
                with self.subTest(action=action, boundary=boundary, event=event_name):
                    self.crash(action, boundary)
        self.assertEqual(counts, {'change': 8, 'rollback': 5, 'retire': 5})
        print('CONTROLLER_PROCESS_DEATH_BOUNDARIES=' + json.dumps(counts, sort_keys=True), flush=True)

    def crash(self, action, boundary):
        root, fd, store, backend, ready = self.fixture()
        if action != 'change':
            with self.hold(store, backend, ready) as controller: controller.change('gpu')
        manager = root / 'generated-manager.json'
        def persist():
            manager.write_text(json.dumps({'video': backend.video, 'mode': backend.worker_mode, 'signals': backend.signals}))
        persist()
        pid = os.fork()
        if pid == 0:
            os.close(fd)
            child_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            count = 0
            def die(event):
                nonlocal count
                persist(); count += 1
                if count == boundary: os._exit(77)
            try:
                child_store = P.PolicyTransaction(child_fd)
                with self.hold(child_store, backend, ready, checkpoint=die) as controller:
                    getattr(controller, action)(*(['gpu'] if action == 'change' else []))
                os._exit(99)
            except BaseException: os._exit(98)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 77)
        state = json.loads(manager.read_bytes())
        backend.video, backend.worker_mode, backend.signals = state['video'], state['mode'], state['signals']
        # New descriptor and new lock OFD: child death must actually release flock.
        fresh_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.hold(P.PolicyTransaction(fresh_fd), backend, ready) as controller:
                result = controller.recover()
                if result['phase'] in ('accepted', 'no-operation', 'retired'):
                    self.assertEqual(backend.worker_mode, 'gpu')
                    if result['phase'] == 'retired': self.assertEqual(result['outcome'], 'accepted')
                else:
                    self.assertEqual(result['phase'], 'restored'); self.assertEqual(backend.worker_mode, 'cpu')
                    self.assertFalse(os.path.lexists(root / P.POLICY))
                self.assertLessEqual(len(backend.signals), 2)
                if action == 'retire': self.assertEqual(len(backend.signals), 1)
        finally: os.close(fresh_fd)


if __name__ == '__main__': unittest.main()
