# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import stat
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('platform_display', Path(__file__).with_name('platform-display.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Display(unittest.TestCase):
    def fixture(self, *, wrong=None, after_wrong=None, failure=None):
        requests, transactions = [], []
        client = Mock()

        def request():
            item = SimpleNamespace(strings=[])
            item.append_string16 = item.strings.append
            requests.append(item)
            return item

        shown = False

        def transact(code, value):
            nonlocal shown
            transactions.append((code, tuple(value.strings)))
            reader = Mock()
            reader.read_int32.return_value = (True, 0)
            if code == P.GETPROP:
                key, default = value.strings
                self.assertEqual(default, '')
                overrides = (after_wrong if shown else wrong) or {}
                reader.read_string16.return_value = overrides.get(key, P.EXPECTED[key])
            elif code == P.SHOW_FULL_UI:
                self.assertEqual(value.strings, [])
                shown = True
                if failure == 'transport': return None, -1
                if failure == 'empty': return None, 0
                if failure == 'exception': reader.read_int32.return_value = (True, -1)
                if failure == 'invalid': reader.read_int32.return_value = (False, 0)
            else:
                self.fail('Unexpected write or transaction')
            return SimpleNamespace(init_reader=lambda: reader), 0

        client.new_request.side_effect = request
        client.transact_sync_reply.side_effect = transact
        manager, remote = Mock(), object()
        manager.is_present.return_value = True
        manager.get_service_sync.return_value = (remote, 0)
        binding = SimpleNamespace(ServiceManager=Mock(return_value=manager), Client=Mock(return_value=client))
        return binding, manager, remote, transactions

    def test_fixed_protocol_interface_and_argumentless_publisher_operation(self):
        binding, manager, remote, transactions = self.fixture()
        guard = Mock()
        result = P.request_full_ui(binding, guard)
        binding.ServiceManager.assert_called_once_with(P.DEVICE, 'aidl6', 'aidl3')
        manager.get_service_sync.assert_called_once_with('waydroidplatform')
        binding.Client.assert_called_once_with(remote, 'id.waydro.waydroid.IPlatform')
        reads = [(1, (k, '')) for k in P.EXPECTED]
        self.assertEqual(transactions, reads + [(15, ())] + reads)
        self.assertEqual(guard.call_count, 3)
        self.assertTrue(result['fullUiRequested'])
        for flag in ('displayVerified', 'mediaPlaybackVerified', 'configurationWritten', 'policyWritten', 'servicesRegistered', 'installable'):
            self.assertFalse(result[flag])

    def test_each_foreign_property_prevents_display_request(self):
        for key in P.EXPECTED:
            binding, _, _, transactions = self.fixture(wrong={key: 'foreign'})
            with self.assertRaises(ValueError): P.request_full_ui(binding, Mock())
            self.assertNotIn(15, [code for code, _ in transactions])

    def test_missing_or_failed_platform_prevents_any_transaction(self):
        for response in ((None, 0), (object(), -1)):
            binding, manager, _, transactions = self.fixture()
            manager.get_service_sync.return_value = response
            with self.assertRaises(ValueError): P.request_full_ui(binding, Mock())
            self.assertEqual(transactions, [])

    def test_missing_manager_prevents_remote_access(self):
        binding, manager, _, transactions = self.fixture()
        manager.is_present.return_value = False
        with self.assertRaises(ValueError): P.request_full_ui(binding, Mock())
        manager.get_service_sync.assert_not_called()
        self.assertEqual(transactions, [])

    def test_unknown_method_transport_and_exception_fail_without_fallback(self):
        for failure in ('transport', 'empty', 'exception', 'invalid'):
            binding, _, _, transactions = self.fixture(failure=failure)
            with self.assertRaises(ValueError): P.request_full_ui(binding, Mock())
            self.assertEqual(transactions[-1], (15, ()))
            self.assertEqual(sum(code == 15 for code, _ in transactions), 1)

    def test_postcondition_drift_is_not_success(self):
        binding, _, _, _ = self.fixture(after_wrong={'waydroid.active_apps': 'foreign'})
        with self.assertRaises(ValueError): P.request_full_ui(binding, Mock())

    def test_device_fences_before_discovery_before_request_and_after(self):
        for fail_at in (1, 2, 3):
            binding, _, _, transactions = self.fixture()
            guard = Mock(side_effect=[None] * (fail_at - 1) + [ValueError('device changed')])
            with self.assertRaises(ValueError): P.request_full_ui(binding, guard)
            self.assertEqual(sum(code == 15 for code, _ in transactions), int(fail_at == 3))

    def device_fixture(self):
        link = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=0, st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0)
        device = SimpleNamespace(st_dev=3, st_ino=4, st_rdev=5, st_mode=stat.S_IFCHR | 0o666, st_uid=0, st_gid=0)
        return link, device

    @unittest.skipUnless(P.sys.platform == 'linux', 'Linux BinderFS open flags required')
    def test_only_fixed_root_binderfs_alias_is_opened_and_fenced(self):
        link, device = self.device_fixture()
        with patch.object(P.os, 'lstat', side_effect=lambda p: link if p == P.DEVICE else device), \
                patch.object(P.os, 'readlink', return_value=P.DEVICE_TARGET), \
                patch.object(P.os, 'open', return_value=99) as opened, \
                patch.object(P.os, 'fstat', return_value=device), patch.object(P.os, 'close') as closed:
            fd, guard = P.device_guard()
            self.assertEqual(fd, 99)
            opened.assert_called_once_with(P.DEVICE_TARGET, P.os.O_RDWR | P.os.O_NOFOLLOW | P.os.O_NONBLOCK)
            guard()
            changed = SimpleNamespace(**vars(link)); changed.st_ino += 1
            with patch.object(P.os, 'lstat', side_effect=lambda p: changed if p == P.DEVICE else device):
                with self.assertRaises(ValueError): guard()
            closed.assert_not_called()

    def test_foreign_alias_is_refused_before_open(self):
        link, _ = self.device_fixture()
        with patch.object(P.os, 'lstat', return_value=link), \
                patch.object(P.os, 'readlink', return_value='/dev/foreign'), patch.object(P.os, 'open') as opened:
            with self.assertRaises(ValueError): P.device_guard()
            opened.assert_not_called()

    @unittest.skipUnless(P.sys.platform == 'linux', 'Linux BinderFS open flags required')
    def test_failed_device_admission_closes_held_descriptor(self):
        link, device = self.device_fixture(); device.st_uid = 1000
        with patch.object(P.os, 'lstat', return_value=link), \
                patch.object(P.os, 'readlink', return_value=P.DEVICE_TARGET), \
                patch.object(P.os, 'open', return_value=99), patch.object(P.os, 'fstat', return_value=device), \
                patch.object(P.os, 'close') as closed:
            with self.assertRaises(ValueError): P.device_guard()
            closed.assert_called_once_with(99)


if __name__ == '__main__': unittest.main(verbosity=2)
