# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('platform_preflight', Path(__file__).with_name('platform-binder-preflight.py'))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)


class Platform(unittest.TestCase):
    def fixture(self):
        reader = Mock()
        reader.read_int32.return_value = (True, 0)
        reader.read_string16.return_value = '36'
        reply = Mock(); reply.init_reader.return_value = reader
        client = Mock(); client.transact_sync_reply.return_value = (reply, 0)
        remote, manager = object(), Mock()
        manager.is_present.return_value = True
        manager.get_service_sync.return_value = (remote, 0)
        binding = SimpleNamespace(ServiceManager=Mock(return_value=manager), Client=Mock(return_value=client))
        return binding, manager, client, reader, remote

    def test_positive_uses_exact_read_only_transaction(self):
        binding, manager, client, reader, remote = self.fixture()
        result = P.inspect(binding)
        binding.ServiceManager.assert_called_once_with('/dev/anbox-binder', 'aidl6', 'aidl3')
        manager.get_service_sync.assert_called_once_with('waydroidplatform')
        binding.Client.assert_called_once_with(remote, P.INTERFACE)
        self.assertEqual(client.new_request.return_value.append_string16.call_args_list[0].args, ('ro.build.version.sdk',))
        self.assertEqual(client.new_request.return_value.append_string16.call_args_list[1].args, ('',))
        client.transact_sync_reply.assert_called_once_with(1, client.new_request.return_value)
        self.assertEqual(result['sdk'], 36)
        self.assertFalse(result['propertiesWritten']); self.assertFalse(result['servicesRegistered'])
        self.assertFalse(result['configurationWritten']); self.assertFalse(result['displayVerified'])

    def test_manager_missing(self):
        binding, manager, client, _, _ = self.fixture()
        manager.is_present.return_value = False
        with self.assertRaises(ValueError): P.inspect(binding)
        client.transact_sync_reply.assert_not_called()

    def test_remote_missing(self):
        binding, manager, client, _, _ = self.fixture()
        manager.get_service_sync.return_value = (None, 0)
        with self.assertRaises(ValueError): P.inspect(binding)
        binding.Client.assert_not_called()

    def test_transport_failure(self):
        binding, _, client, _, _ = self.fixture()
        client.transact_sync_reply.return_value = (None, -1)
        with self.assertRaises(ValueError): P.inspect(binding)

    def test_exception_or_invalid_reader(self):
        for header in ((False, 0), (True, -1)):
            binding, _, _, reader, _ = self.fixture()
            reader.read_int32.return_value = header
            with self.assertRaises(ValueError): P.inspect(binding)

    def test_other_sdk_never_accepted(self):
        for sdk in ('35', '37', '', '36private-sentinel'):
            binding, _, _, reader, _ = self.fixture()
            reader.read_string16.return_value = sdk
            with self.assertRaises(ValueError): P.inspect(binding)


if __name__ == '__main__': unittest.main(verbosity=2)
