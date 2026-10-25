# SPDX-License-Identifier: GPL-3.0-only
from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('session_attach', Path(__file__).with_name('platform-session-attach.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Attachment(unittest.TestCase):
    def fixture(self):
        manager, platform, monitor, drivers, glib = [Mock() for _ in range(5)]
        manager.is_present.return_value = True
        manager.get_service_sync.return_value = (None, 0)
        binding = SimpleNamespace(ServiceManager=Mock(return_value=manager))
        platform.get_service.return_value.getprop.side_effect = lambda key, default: {
            'ro.build.version.sdk': '36', 'sys.boot_completed': '1'}[key]
        glib.timeout_add_seconds.return_value = 10
        glib.unix_signal_add.side_effect = [11, 12, 13]
        adapt = Mock(return_value=nullcontext())
        return binding, platform, monitor, drivers, adapt, glib

    @unittest.skipUnless(hasattr(P.signal, 'SIGUSR1'), 'Native Unix signal constants required')
    def test_actual_callback_gate_without_display_setter_or_session_start(self):
        items = self.fixture()
        loop = Mock()
        def add(args, unlocked, changed):
            args.userMonitorLoop = loop
            changed(1, 'PRIVATE_SENTINEL', 0)
            unlocked(0)
        items[2].add_service.side_effect = add
        result = P.probe(*items)
        self.assertTrue(result['callbackReady']); self.assertEqual(result['callbacks'], 1)
        self.assertFalse(result['probeExpired']); self.assertFalse(result['sessionRestarted'])
        self.assertNotIn('PRIVATE_SENTINEL', str(result))
        loop.quit.assert_called_once()
        items[1].get_service.return_value.setprop.assert_not_called()
        items[0].ServiceManager.assert_called_once_with('/dev/anbox-binder', 'aidl6', 'aidl3')
        self.assertEqual(items[5].source_remove.call_count, 4)

    def test_existing_or_uncertain_service_never_replaced(self):
        for value in ((object(), 0), (None, -1), (object(), -1)):
            items = self.fixture()
            items[0].ServiceManager.return_value.get_service_sync.return_value = value
            with self.assertRaises(ValueError): P.probe(*items)
            items[2].add_service.assert_not_called()

    def test_absent_manager_never_registers(self):
        items = self.fixture(); items[0].ServiceManager.return_value.is_present.return_value = False
        with self.assertRaises(ValueError): P.probe(*items)
        items[2].add_service.assert_not_called()

    @unittest.skipUnless(hasattr(P.signal, 'SIGUSR1'), 'Native Unix signal constants required')
    def test_wrong_sdk_refused_and_sources_cleaned(self):
        items = self.fixture(); items[1].get_service.return_value.getprop.return_value = '35'
        items[1].get_service.return_value.getprop.side_effect = None
        with self.assertRaises(ValueError): P.probe(*items)
        items[2].add_service.assert_not_called()
        self.assertEqual(items[5].source_remove.call_count, 4)

    @unittest.skipUnless(hasattr(P.signal, 'SIGUSR1'), 'Native Unix signal constants required')
    def test_timeout_is_negative_and_not_removed_twice(self):
        items = self.fixture(); loop = Mock()
        def add(args, unlocked, changed):
            args.userMonitorLoop = loop
            items[5].timeout_add_seconds.call_args.args[1]()
        items[2].add_service.side_effect = add
        result = P.probe(*items)
        self.assertFalse(result['callbackReady']); self.assertTrue(result['probeExpired'])
        self.assertEqual(items[5].source_remove.call_count, 3)
        loop.quit.assert_called_once()

    @unittest.skipUnless(hasattr(P.signal, 'SIGUSR1'), 'Native Unix signal constants required')
    def test_foreign_user_or_bool_does_not_mark_ready(self):
        items = self.fixture()
        def add(args, unlocked, changed):
            args.userMonitorLoop = Mock()
            for uid in (1, -1, True, '0'): unlocked(uid)
        items[2].add_service.side_effect = add
        result = P.probe(*items)
        self.assertFalse(result['callbackReady']); self.assertEqual(result['callbacks'], 0)


if __name__ == '__main__': unittest.main(verbosity=2)
