# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import unittest


class MemoryStore:
    def __init__(self, stage='ready', boot='old', since=50):
        self.value = {'stage': stage, 'boot': boot, 'since': since}
        self.writes = 0

    @contextmanager
    def lock(self):
        yield

    def status(self):
        return dict(self.value)

    def stage(self, value):
        self.value = {'stage': value, 'boot': 'current', 'since': 100}
        self.writes += 1


def load():
    source = Path(__file__).with_name('gpu-boot.py')
    if not source.exists():
        raise AssertionError('GPU boot coordinator must exist')
    spec = importlib.util.spec_from_file_location('gpu_boot', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Boot(unittest.TestCase):
    def coordinator(self, store=None, native=None, peers=None, admit=None):
        module = load()
        self.time = 0
        def sleep(seconds):
            self.time += seconds
        return module.Coordinator(store or MemoryStore(), boot=lambda: 'current',
            native=native or (lambda: True), peers=peers or (lambda: (11, 22, 33)),
            admit=admit or (lambda: None), clock=lambda: self.time, sleep=sleep)

    def test_new_boot_arms_once_and_same_boot_does_not_extend_startup_budget(self):
        store = MemoryStore()
        c = self.coordinator(store)
        c.arm()
        self.assertEqual(store.value, {'stage': 'starting', 'boot': 'current', 'since': 100})
        c.arm()
        self.assertEqual(store.writes, 1)

    def test_current_healthy_state_has_no_expiry_or_rearm(self):
        store = MemoryStore(boot='current', since=0)
        c = self.coordinator(store)
        c.arm()
        self.assertTrue(c.verify())
        self.assertEqual(store.writes, 0)

    def test_interrupted_or_restored_states_cannot_be_reclassified_as_fresh_startup(self):
        for stage in ('prepared', 'installing', 'starting', 'recovering', 'restored', 'recovery-required'):
            for boot in ('old', 'current'):
                if (stage, boot) == ('starting', 'current'):
                    continue
                store = MemoryStore(stage, boot)
                before = dict(store.value)
                self.coordinator(store).arm()
                self.assertEqual(store.value, before)
                self.assertEqual(store.writes, 0)

    def test_foreign_profile_or_service_admission_never_writes(self):
        def denied():
            raise ValueError('Foreign baseline')
        store = MemoryStore()
        with self.assertRaises(ValueError):
            self.coordinator(store, admit=denied).arm()
        self.assertEqual(store.writes, 0)

    def test_failed_arm_admission_cannot_block_original_watch_execstart(self):
        module = load()
        def failed_context():
            raise ValueError('Bad or interrupted baseline')
        self.assertFalse(module.arm_guard(failed_context))
        store = MemoryStore('recovery-required', 'old')
        self.assertTrue(module.arm_guard(lambda: self.coordinator(store)))
        self.assertEqual(store.writes, 0)

    def test_native_health_promotes_only_current_armed_generation(self):
        store = MemoryStore('starting', 'current')
        self.assertTrue(self.coordinator(store).verify())
        self.assertEqual(store.value['stage'], 'ready')
        self.assertEqual(store.writes, 1)
        for boot in ('old', 'other'):
            store = MemoryStore('starting', boot)
            with self.assertRaises(ValueError):
                self.coordinator(store).verify()
            self.assertEqual(store.writes, 0)

    def test_peer_restart_during_native_probe_cannot_promote(self):
        store = MemoryStore('starting', 'current')
        values = iter(((11, 22, 33), (11, 22, 34)))
        with self.assertRaises(ValueError):
            self.coordinator(store, peers=lambda: next(values)).verify()
        self.assertEqual(store.writes, 0)

    def test_recovery_wins_over_inflight_native_probe(self):
        store = MemoryStore('starting', 'current')
        def native():
            store.value['stage'] = 'recovering'
            return True
        with self.assertRaises(ValueError):
            self.coordinator(store, native=native).verify()
        self.assertEqual(store.writes, 0)

    def test_cold_native_health_can_become_ready_without_false_fallback(self):
        store = MemoryStore('starting', 'current')
        values = iter((False, False, True))
        self.assertTrue(self.coordinator(store, native=lambda: next(values)).verify())
        self.assertEqual(store.value['stage'], 'ready')
        self.assertGreater(self.time, 0)

    def test_failure_is_bounded_and_leaves_original_watcher_in_control(self):
        store = MemoryStore('starting', 'current')
        with self.assertRaises(ValueError):
            self.coordinator(store, native=lambda: False).verify()
        self.assertEqual(store.writes, 0)
        self.assertLessEqual(self.time, 152)

    def test_supervision_admission_checks_exact_control_process(self):
        module = load()
        module.require_supervision({'ActiveState': 'activating', 'ControlPID': '123', 'MainPID': '0'}, 'arm', 123)
        module.require_supervision({'ActiveState': 'activating', 'ControlPID': '0', 'MainPID': '123'}, 'verify', 123)
        with self.assertRaises(ValueError):
            module.require_supervision({'ActiveState': 'activating', 'ControlPID': '123', 'MainPID': '0'}, 'verify', 123)
        for state in ({'ActiveState': 'active', 'ControlPID': '0', 'MainPID': '123'},
                      {'ActiveState': 'activating', 'ControlPID': '124', 'MainPID': '0'}):
            with self.assertRaises(ValueError):
                module.require_supervision(state, 'arm', 123)

    def test_cli_refuses_overrides_before_protected_reads(self):
        module = load()
        for args in ([], ['--arm', '/other'], ['--apply'], ['--verify', '--force']):
            with self.assertRaises(ValueError):
                module.main(args)

    def test_extra_or_missing_dropins_and_changed_callback_type_cannot_pass(self):
        module = load()
        records = module.expected_settings()
        module.validate_settings(records)
        from copy import deepcopy
        for name, key, value in (
            ('tesla-waydroid-headless-session.service', 'Type', 'simple'),
            ('tesla-waydroid-headless-session.service', 'NotifyAccess', 'none'),
            ('tesla-waydroid-compositor.service', 'DropInPaths', []),
            ('waydroid-container.service', 'ExecStartPre', []),
            ('droiddeck-gpu-recovery.service', 'ExecStart', [['/bin/true', ['/bin/true']]]),
        ):
            changed = deepcopy(records)
            changed[name][key] = value
            with self.assertRaises(ValueError):
                module.validate_settings(changed)
        changed = deepcopy(records)
        changed['tesla-waydroid-headless-session.service']['DropInPaths'].append('/run/systemd/system/foreign.conf')
        with self.assertRaises(ValueError):
            module.validate_settings(changed)

    def test_changed_own_unit_or_dependency_dropin_bytes_are_rejected(self):
        module = load()
        base = Path(__file__).parent
        def read(filename):
            name = 'droiddeck-gpu-boot-ready.service' if filename.name == module.READY else (
                'gpu-boot-arm.conf' if module.GUARD in str(filename) else 'gpu-boot-peer.conf')
            return (base / name).read_bytes()
        module.admit_owned(read)
        for changed in module.OWN_PINS:
            with self.assertRaises(ValueError):
                module.admit_owned(lambda filename: read(filename) + (b'After=\n' if filename == changed else b''))

    def test_new_inactive_unit_can_be_metadata_loaded_without_starting_it(self):
        module = load()
        from types import SimpleNamespace
        from unittest.mock import patch
        records = module.expected_settings()
        manager = SimpleNamespace(LoadUnit=lambda name, timeout: name)
        class Properties:
            def __init__(self, name):
                self.record = records[name]
            def GetAll(self, interface, timeout):
                result = dict(self.record)
                for key in ('ExecStart', 'ExecStartPre'):
                    result[key] = [[*item, False] for item in result[key]]
                return result
        bus = SimpleNamespace(get_object=lambda name, path: path)
        dbus = SimpleNamespace(SystemBus=lambda: bus, Interface=lambda obj, interface:
            manager if interface.endswith('.Manager') else Properties(obj))
        with patch.dict('sys.modules', {'dbus': dbus}):
            module.validate_settings(module.effective_settings())


if __name__ == '__main__':
    unittest.main(verbosity=2)
