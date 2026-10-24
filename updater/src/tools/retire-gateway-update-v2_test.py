# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


R = load('retirement_tested', Path(__file__).with_name('retire-gateway-update-v2.py'))


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private development retirement fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        fixture = load('retirement_fixture', Path(__file__).parents[1] / 'runtime/portable/gateway_update_test.py')
        self.d = fixture.D
        self.f = fixture.Native('test_real_atomic_gateway_apply_and_explicit_rollback_restore_original_inode')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.controller = self.f.root / 'controller'; self.controller.mkdir(mode=0o700)
        self.source = self.f.root / 'original-controller.py'
        self.source.write_bytes(Path(self.d.__file__).read_bytes()); self.source.chmod(0o600)
        self.retired = self.f.root / 'retired'
        self.commands, self.requires = [], b'\n'
        for item in (patch.object(self.d, 'CONTROLLER', self.controller), patch.object(R, 'ROOT', self.retired),
                     patch.object(R, 'SOURCE', self.source), patch.object(self.d.G, 'command', lambda *a, **k: None)):
            item.start(); self.addCleanup(item.stop)
        self.d.arm_boot()
        item = patch.object(self.d, 'inspect_bootstrap', self.f._original_bootstrap)
        item.start(); self.addCleanup(item.stop)
        self.f.prepare(); self.f.transaction().run(); self.d.request_rollback(self.f.digest); self.f.transaction().run()
        self.unit = self.f.transaction().unit
        inactive = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
        self.states = {self.d.RUNTIME: {'ActiveState': 'active', 'MainPID': '100', 'ControlPID': '0'},
            self.f.g.WATCHER: {'ActiveState': 'active', 'MainPID': '101', 'ControlPID': '0'},
            self.f.g.WORKER: dict(inactive), self.d.BOOT: {'ActiveState': 'active', 'MainPID': '0', 'ControlPID': '0'},
            self.unit: {'ActiveState': 'active', 'MainPID': '0', 'ControlPID': '0'}}
        def command(args, **_kwargs):
            self.commands.append(args)
            if args[1] == 'show': return type('Result', (), {'stdout': self.requires})()
            if args[1] == 'stop':
                self.assertEqual(args[2:], [self.d.BOOT, self.unit])
                for name in args[2:]: self.states[name] = dict(inactive)
            return type('Result', (), {'stdout': b''})()
        for item in (patch.object(self.d.G, 'command', command),
                     patch.object(self.f.g, 'unit', lambda name: copy.deepcopy(self.states[name])),
                     patch.object(self.f.g, 'require_quiet', self.d.G.require_quiet)):
            item.start(); self.addCleanup(item.stop)

    def test_retains_actual_dependency_inodes_without_restarting_original_services(self):
        links = self.d.boot_links(self.f.context)
        originals = [path.lstat().st_ino for path in links]
        result = R.retire(self.d)
        self.assertTrue(result['originalRuntimeUnchanged'])
        self.assertTrue(result['originalGpuWatcherUnchanged'])
        self.assertEqual([path.lstat().st_ino for path in sorted((self.retired / 'links').iterdir())], originals)
        self.assertTrue(all(not os.path.lexists(path) for path in links))
        self.assertEqual(self.states[self.d.RUNTIME]['MainPID'], '100')
        self.assertEqual(self.states[self.f.g.WATCHER]['MainPID'], '101')
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)
        before = list(self.commands)
        with self.assertRaises((ValueError, FileNotFoundError)): R.retire(self.d)
        self.assertEqual(self.commands, before)

    def test_pending_rollback_refuses_before_any_dependency_move(self):
        (self.f.transactions / self.f.digest / 'rolled-back.json').unlink()
        with self.assertRaises(ValueError): R.retire(self.d)
        self.assertFalse(self.retired.exists())
        self.assertEqual(self.commands, [])

    def test_foreign_equal_target_link_refuses_before_retirement(self):
        link = self.d.boot_links(self.f.context)[0]
        link.rename(link.with_name('retained-original'))
        link.symlink_to(self.d.SYSTEMD / self.d.BOOT)
        with self.assertRaises(ValueError): R.retire(self.d)
        self.assertFalse(self.retired.exists())
        self.assertEqual(self.commands, [])

    def test_runtime_dependency_must_be_detached_before_old_barrier_stop(self):
        self.requires = (self.d.BOOT + '\n').encode()
        with self.assertRaises(ValueError): R.retire(self.d)
        self.assertFalse(any(command[1] == 'stop' for command in self.commands))
        self.assertTrue((self.retired / 'intent.json').exists())
        self.assertFalse((self.retired / 'complete.json').exists())
        self.assertEqual(self.states[self.d.RUNTIME]['MainPID'], '100')

    def test_running_updater_refuses_before_retirement(self):
        self.states[self.unit]['MainPID'] = '102'
        with self.assertRaises(ValueError): R.retire(self.d)
        self.assertFalse(self.retired.exists())
        self.assertEqual(self.commands, [])


if __name__ == '__main__': unittest.main()
