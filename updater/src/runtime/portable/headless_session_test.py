# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock

spec = importlib.util.spec_from_file_location('headless_session', Path(__file__).with_name('headless-session.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Session(unittest.TestCase):
    def fixture(self):
        tools, session, platform, monitor, drivers, notify = [Mock() for _ in range(6)]
        service = platform.get_service.return_value
        values = {'ro.build.version.sdk': '36', 'sys.boot_completed': '1', 'waydroid.active_apps': 'Waydroid'}
        service.getprop.side_effect = lambda key, _: values[key]
        state = {'adapted': False, 'restored': False}

        @contextmanager
        def adapt(*modules):
            self.assertEqual(modules, (platform, monitor, drivers))
            state['adapted'] = True
            try: yield
            finally: state['restored'] = True

        return tools, session, platform, monitor, drivers, adapt, notify, state, values

    def test_readiness_requires_real_callback_and_uses_only_stable_full_ui_property(self):
        items = self.fixture()
        tools, session, platform, _, _, _, notify, state, _ = items
        def start(args, *, unlocked_cb, background):
            self.assertFalse(background)
            self.assertTrue(state['adapted'])
            notify.assert_not_called()
            platform.get_service.return_value.setprop.assert_not_called()
            unlocked_cb(); unlocked_cb()
        session.start.side_effect = start
        P.run_session(*items[:7])
        notify.assert_called_once_with()
        self.assertTrue(state['restored'])
        tools.prep_args.assert_called_once()
        service = platform.get_service.return_value
        self.assertEqual(service.setprop.call_count, 2)
        service.setprop.assert_called_with('waydroid.active_apps', 'Waydroid')
        service.settingsPutString.assert_not_called()
        service.showFullUI.assert_not_called()

    def test_no_callback_never_reports_ready(self):
        items = self.fixture()
        P.run_session(*items[:7])
        items[6].assert_not_called()
        items[2].get_service.return_value.setprop.assert_not_called()

    def test_wrong_sdk_refused_in_callback_before_readiness(self):
        items = self.fixture(); items[-1]['ro.build.version.sdk'] = 'foreign'
        items[1].start.side_effect = lambda args, unlocked_cb, background: unlocked_cb()
        with self.assertRaises(ValueError): P.run_session(*items[:7])
        items[6].assert_not_called()
        self.assertTrue(items[-2]['restored'])

    def test_session_starts_before_platform_discovery_and_callback_does_not_block_boot(self):
        items = self.fixture(); items[-1]['sys.boot_completed'] = '0'
        def start(args, unlocked_cb, background):
            items[2].get_service.assert_not_called()
            unlocked_cb()
        items[1].start.side_effect = start
        P.run_session(*items[:7])
        items[6].assert_called_once()
        self.assertNotIn(('sys.boot_completed', ''), [call.args for call in items[2].get_service.return_value.getprop.call_args_list])

    def test_callback_postcondition_failure_never_reports_ready(self):
        items = self.fixture(); items[-1]['waydroid.active_apps'] = 'foreign'
        items[1].start.side_effect = lambda args, unlocked_cb, background: unlocked_cb()
        with self.assertRaises(ValueError): P.run_session(*items[:7])
        items[6].assert_not_called()
        self.assertTrue(items[-2]['restored'])

    def test_notify_failure_never_marks_ready(self):
        items = self.fixture(); items[6].side_effect = OSError('fixture')
        items[1].start.side_effect = lambda args, unlocked_cb, background: unlocked_cb()
        with self.assertRaises(OSError): P.run_session(*items[:7])
        self.assertTrue(items[-2]['restored'])

    @unittest.skipUnless(hasattr(P.socket, 'AF_UNIX'), 'Native Unix datagram constants required')
    def test_notify_is_fixed_datagram_not_child_process_or_caller_payload(self):
        factory = MagicMock()
        channel = factory.return_value.__enter__.return_value
        P.send_ready('/run/systemd/notify', factory)
        factory.assert_called_once_with(P.socket.AF_UNIX, P.socket.SOCK_DGRAM)
        channel.connect.assert_called_once_with('/run/systemd/notify')
        channel.sendall.assert_called_once_with(b'READY=1\nSTATUS=Android platform user callback ready')
        for target in ('/tmp/foreign', '@foreign', None):
            with self.assertRaises(ValueError): P.send_ready(target, factory)
        self.assertEqual(factory.call_count, 1)


if __name__ == '__main__': unittest.main(verbosity=2)
