# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('host_compat', Path(__file__).with_name('host-platform-compat.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class Compatibility(unittest.TestCase):
    def fixture(self):
        platform = SimpleNamespace(INTERFACE='lineageos.waydroid.IPlatform', SERVICE_NAME='waydroidplatform',
            TRANSACTION_getprop=1, TRANSACTION_setprop=2, unrelated='preserved')
        monitor = SimpleNamespace(INTERFACE='lineageos.waydroid.IUserMonitor', SERVICE_NAME='waydroidusermonitor',
            TRANSACTION_userUnlocked=1, TRANSACTION_packageStateChanged=2)
        def load(args):
            args.BINDER_DRIVER = 'anbox-binder'
            args.VNDBINDER_DRIVER = 'anbox-vndbinder'
            args.HWBINDER_DRIVER = 'anbox-hwbinder'
            args.BINDER_PROTOCOL = 'aidl3'
            args.SERVICE_MANAGER_PROTOCOL = 'aidl3'
        drivers = SimpleNamespace(loadBinderNodes=Mock(side_effect=load))
        return platform, monitor, drivers

    def test_exact_namespaces_and_protocol_override_are_process_local(self):
        platform, monitor, drivers = self.fixture()
        original = drivers.loadBinderNodes
        args = SimpleNamespace(config='/fixed/read-only.cfg')
        with P.adapt_host(platform, monitor, drivers):
            self.assertEqual(platform.INTERFACE, 'id.waydro.waydroid.IPlatform')
            self.assertEqual(monitor.INTERFACE, 'id.waydro.waydroid.IUserMonitor')
            drivers.loadBinderNodes(args)
            self.assertEqual(args.SERVICE_MANAGER_PROTOCOL, 'aidl6')
            self.assertEqual(args.BINDER_PROTOCOL, 'aidl3')
            original.assert_called_once_with(args)
        self.assertIs(drivers.loadBinderNodes, original)
        self.assertEqual(platform.INTERFACE, 'lineageos.waydroid.IPlatform')
        self.assertEqual(monitor.INTERFACE, 'lineageos.waydroid.IUserMonitor')
        self.assertEqual(platform.unrelated, 'preserved')

    def test_exception_restores_module_adaptations(self):
        platform, monitor, drivers = self.fixture()
        original = drivers.loadBinderNodes
        with self.assertRaises(RuntimeError):
            with P.adapt_host(platform, monitor, drivers): raise RuntimeError('fixture')
        self.assertIs(drivers.loadBinderNodes, original)
        self.assertTrue(platform.INTERFACE.startswith('lineageos.'))
        self.assertTrue(monitor.INTERFACE.startswith('lineageos.'))

    def test_foreign_protocol_or_device_never_overridden(self):
        for key in ('BINDER_DRIVER', 'VNDBINDER_DRIVER', 'HWBINDER_DRIVER', 'BINDER_PROTOCOL', 'SERVICE_MANAGER_PROTOCOL'):
            platform, monitor, drivers = self.fixture()
            original = drivers.loadBinderNodes
            def changed(args):
                original(args)
                setattr(args, key, 'foreign')
            drivers.loadBinderNodes = changed
            with P.adapt_host(platform, monitor, drivers):
                args = SimpleNamespace()
                with self.assertRaises(ValueError): drivers.loadBinderNodes(args)
                self.assertEqual(getattr(args, key), 'foreign')
            self.assertIs(drivers.loadBinderNodes, changed)

    def test_changed_namespace_or_transaction_contract_is_refused(self):
        for target, key in ((0, 'INTERFACE'), (0, 'SERVICE_NAME'), (0, 'TRANSACTION_getprop'),
                            (0, 'TRANSACTION_setprop'), (1, 'INTERFACE'), (1, 'SERVICE_NAME'),
                            (1, 'TRANSACTION_userUnlocked'), (1, 'TRANSACTION_packageStateChanged')):
            items = self.fixture()
            setattr(items[target], key, 'foreign')
            original = items[2].loadBinderNodes
            with self.assertRaises(ValueError):
                with P.adapt_host(*items): self.fail('Foreign interface admitted')
            self.assertIs(items[2].loadBinderNodes, original)
            self.assertEqual(getattr(items[target], key), 'foreign')

    def test_nested_adaptation_refused_without_disrupting_outer_scope(self):
        items = self.fixture()
        with P.adapt_host(*items):
            outer = items[2].loadBinderNodes
            with self.assertRaises(ValueError):
                with P.adapt_host(*items): self.fail('Nested scope admitted')
            self.assertIs(items[2].loadBinderNodes, outer)
            self.assertEqual(items[0].INTERFACE, 'id.waydro.waydroid.IPlatform')

    def test_original_load_failure_is_not_hidden(self):
        items = self.fixture()
        items[2].loadBinderNodes.side_effect = ValueError('fixture')
        original = items[2].loadBinderNodes
        with self.assertRaises(ValueError):
            with P.adapt_host(*items): items[2].loadBinderNodes(SimpleNamespace())
        self.assertIs(items[2].loadBinderNodes, original)


if __name__ == '__main__': unittest.main(verbosity=2)
