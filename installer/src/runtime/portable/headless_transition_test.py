# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack, nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('headless_transition', Path(__file__).with_name('headless-transition.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


@unittest.skipUnless(P.sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Native root-owned filesystem recovery test required')
class Transition(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='deck-headless-test-', dir='/root')))
        root, system, state, config = (self.tmp / x for x in ('component', 'system', 'state', 'config'))
        root.mkdir(); system.mkdir()
        (system / P.UNIT).write_bytes(b'original unit\n')
        (system / P.COMPOSITOR).write_bytes(b'original compositor\n')
        (root / 'headless-session.py').write_bytes(b'fixed wrapper\n')
        config.write_bytes(b'GPU\n')
        for name, value in {'ROOT': root, 'SYSTEM': system, 'STATE': state, 'CONFIG': config,
            'BASE': hashlib.sha256(b'original unit\n').hexdigest(),
            'COMPOSITOR_HASH': hashlib.sha256(b'original compositor\n').hexdigest(),
            'WRAPPER': hashlib.sha256(b'fixed wrapper\n').hexdigest(), 'GPU': hashlib.sha256(b'GPU\n').hexdigest()}.items():
            self.stack.enter_context(patch.object(P, name, value))
        self.calls, self.restart_error, self.native, self.stopped = [], False, True, set()
        self.stage = 'ready'
        store = SimpleNamespace(lock=lambda: nullcontext(), status=lambda: {'stage': self.stage},
                                stage=lambda value: setattr(self, 'stage', value))
        self.stack.enter_context(patch.object(P, 'gpu_store', return_value=store))
        self.stack.enter_context(patch.object(P, 'wait_native', side_effect=lambda: self.native))
        self.stack.enter_context(patch.object(P, 'container', side_effect=[[100, 200], [101, 300], [101, 300]]))
        self.stack.enter_context(patch.object(P, 'session_arguments', side_effect=lambda _: [b'/usr/bin/python3', b'-B', os.fsencode(root / 'headless-session.py')]))
        self.stack.enter_context(patch.object(P, 'unit', side_effect=self.unit))
        self.stack.enter_context(patch.object(P, 'command', side_effect=self.command))
        self.stack.enter_context(patch.object(P, 'compositor_ready'))
        self.stack.enter_context(patch.object(P, 'empty_session', return_value=True))

    def unit(self, name):
        if name == 'tesla-waydroid-runtime.service' or name in self.stopped:
            return {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0', 'SubState': 'dead'}
        return {'ActiveState': 'active', 'MainPID': str(os.getpid()) if name == P.WORKER else '999',
                'ControlPID': '0', 'SubState': 'running'}

    def command(self, args, timeout=80):
        self.calls.append(args)
        if args[:2] == ['/usr/bin/node', '/opt/droiddeck/gpu-recovery/runtime/portable/gpu-native.js']:
            return json.dumps({'nativeGpuVerified': self.native})
        if args[1] == 'stop': self.stopped.update(args[2:])
        if args[1] == 'start': self.stopped.discard(args[2])
        if args[1:3] == ['start', P.UNIT] and self.restart_error:
            self.restart_error = False
            raise subprocess.CalledProcessError(1, args)
        return ''

    def receipt(self):
        return json.loads(P.read(P.STATE / 'receipt.json'))

    def test_success_retains_original_unit_and_never_expires_accepted_session(self):
        P.apply()
        self.assertEqual(self.receipt()['stage'], 'accepted')
        self.assertEqual(P.read(P.SYSTEM / P.UNIT), b'original unit\n')
        P.owned(self.receipt(), P.SYSTEM / P.TARGET)
        before = list(self.calls)
        P.recover_pending()
        self.assertEqual(self.calls, before)

    def test_startup_failure_restores_owned_dropin_without_deletion(self):
        self.restart_error = True
        with self.assertRaises(subprocess.CalledProcessError): P.apply()
        self.assertEqual(self.receipt()['stage'], 'installed')
        P.recover_pending()
        self.assertEqual(self.receipt()['stage'], 'restored')
        self.assertFalse((P.SYSTEM / P.TARGET).exists())
        P.owned(self.receipt(), P.SYSTEM / P.RETIRED)
        self.assertIn(['/usr/bin/systemctl', 'start', P.UNIT], self.calls)

    def test_native_failure_also_restores(self):
        self.native = False
        with self.assertRaises(ValueError): P.apply()
        self.native = True
        P.recover_pending()
        self.assertEqual(self.receipt()['stage'], 'restored')

    def test_interruption_after_publish_before_receipt_update_recovers(self):
        record = P.record
        def interrupted(value):
            if value['stage'] == 'installed': raise RuntimeError('interrupted')
            record(value)
        with patch.object(P, 'record', side_effect=interrupted):
            with self.assertRaises(RuntimeError): P.apply()
        self.assertEqual(self.receipt()['stage'], 'prepared')
        P.recover_pending()
        self.assertEqual(self.receipt()['stage'], 'restored')

    def test_interrupted_recovery_after_retirement_resumes_reload_and_start(self):
        self.restart_error = True
        with self.assertRaises(subprocess.CalledProcessError): P.apply()
        command = self.command
        def interrupted(args, timeout=80):
            if args[1:] == ['daemon-reload']: raise RuntimeError('interrupted')
            return command(args, timeout)
        with patch.object(P, 'command', side_effect=interrupted):
            with self.assertRaises(RuntimeError): P.recover_pending()
        self.assertFalse((P.SYSTEM / P.TARGET).exists())
        P.recover_pending()
        self.assertEqual(self.receipt()['stage'], 'restored')

    def test_foreign_dropin_is_preserved_and_recovery_refused(self):
        self.restart_error = True
        with self.assertRaises(subprocess.CalledProcessError): P.apply()
        target = P.SYSTEM / P.TARGET / '60-droiddeck-compat.conf'
        target.write_bytes(b'foreign\n')
        with self.assertRaises(ValueError): P.recover_pending()
        self.assertEqual(target.read_bytes(), b'foreign\n')

    def test_existing_namespace_and_replay_refused(self):
        for leaf in (P.TARGET, P.PENDING, P.RETIRED):
            (P.SYSTEM / leaf).mkdir()
            with self.assertRaises(ValueError): P.apply()
            self.assertFalse(P.STATE.exists())
            (P.SYSTEM / leaf).rmdir()
        P.apply()
        with self.assertRaises(ValueError): P.apply()

    def test_changed_source_is_refused_before_state_creation(self):
        (P.ROOT / 'headless-session.py').write_bytes(b'foreign\n')
        with self.assertRaises(ValueError): P.apply()
        self.assertFalse(P.STATE.exists())

    def test_container_reuse_or_change_does_not_get_accepted(self):
        with patch.object(P, 'container', side_effect=[[100, 200], [101, 300], [101, 301]]):
            with self.assertRaises(ValueError): P.apply()
        self.assertEqual(self.receipt()['stage'], 'installed')
        P.recover_pending()
        self.assertEqual(self.receipt()['stage'], 'restored')

    def test_recovery_never_restarts_against_gpu_guard_cpu_restoration(self):
        self.restart_error = True
        with self.assertRaises(subprocess.CalledProcessError): P.apply()
        P.CONFIG.write_bytes(b'CPU restoration\n')
        before = len(self.calls)
        P.recover_pending()
        self.assertNotIn(['/usr/bin/systemctl', 'start', P.UNIT], self.calls[before:])
        self.assertEqual(self.receipt()['stage'], 'restored')

    def test_partial_prepublication_state_does_not_touch_live_unit(self):
        record = P.record
        def interrupted(value):
            record(value)
            raise RuntimeError('interrupted')
        with patch.object(P, 'record', side_effect=interrupted):
            with self.assertRaises(RuntimeError): P.apply()
        P.recover_pending()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.receipt()['stage'], 'restored')
        self.assertTrue((P.SYSTEM / P.PENDING).exists())

    def test_graph_is_stopped_before_fresh_compositor_and_root_and_session(self):
        P.cold_start()
        self.assertEqual(self.calls, [
            ['/usr/bin/systemctl', 'stop', P.UNIT, 'waydroid-container.service', P.COMPOSITOR],
            ['/usr/bin/systemctl', 'start', P.COMPOSITOR],
            ['/usr/bin/systemctl', 'start', 'waydroid-container.service'],
            ['/usr/bin/systemctl', 'start', P.UNIT]])
        P.compositor_ready.assert_called_once_with()
        P.empty_session.assert_called_once_with()

    def test_stale_owner_refuses_duplicate_start(self):
        with patch.object(P, 'empty_session', return_value=False):
            with self.assertRaisesRegex(ValueError, 'already tracks'): P.cold_start()
        self.assertNotIn(['/usr/bin/systemctl', 'start', P.UNIT], self.calls)

    def test_any_unstopped_graph_peer_refuses_restart(self):
        for name in (P.UNIT, 'waydroid-container.service', P.COMPOSITOR):
            with self.subTest(name=name):
                self.calls.clear()
                real = self.unit
                with patch.object(P, 'unit', side_effect=lambda unit: {'MainPID': '12', 'ControlPID': '0'} if unit == name else real(unit)):
                    with self.assertRaisesRegex(ValueError, 'quiescent'): P.cold_start()
                self.assertEqual(len(self.calls), 1)

    def test_restarted_compositor_does_not_get_accepted(self):
        real = self.unit
        observations = 0
        def changed(name):
            nonlocal observations
            value = real(name)
            if name == P.COMPOSITOR and value['ActiveState'] == 'active':
                observations += 1
                value['MainPID'] = str(200 + observations)
            return value
        with patch.object(P, 'unit', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed'): P.cold_start()

    def test_no_socket_never_starts_android(self):
        with patch.object(P, 'compositor_ready', side_effect=OSError), patch.object(P.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(ValueError, 'startup unavailable'): P.cold_start()
        self.assertNotIn(['/usr/bin/systemctl', 'start', 'waydroid-container.service'], self.calls)

    def test_foreign_compositor_refused_before_any_change(self):
        (P.SYSTEM / P.COMPOSITOR).write_bytes(b'foreign\n')
        with self.assertRaisesRegex(ValueError, 'baseline changed'): P.apply()
        self.assertFalse(P.STATE.exists())
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main(verbosity=2)
