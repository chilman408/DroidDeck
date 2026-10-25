# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os

def load(name, path):
    s = importlib.util.spec_from_file_location(name, path); v = importlib.util.module_from_spec(s); s.loader.exec_module(v); return v

R = load('retire_chain_tested', Path(__file__).with_name('retire-gateway-update-v3.py'))

@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private closed-chain retirement fixture')
class Native(unittest.TestCase):
    def setUp(self):
        previous = load('original_retirement_fixture', Path(__file__).with_name('retire-gateway-update-v2_test.py'))
        self.f = previous.Native('test_retains_actual_dependency_inodes_without_restarting_original_services')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.d = self.f.d
        self.c = SimpleNamespace(B=self.d, records=lambda: [{'original': True}],
            current_view=lambda _: self.f.f.transaction(), inspect_controller=lambda: self.f.f.context,
            P=self.d.P, E=self.d.E, G=self.d.G, H=self.d.H, T=self.d.T)
        for item in (patch.object(R, 'SOURCE', self.f.source), patch.object(R, 'SOURCE_PIN', self.d.P.sha(self.f.source.read_bytes())),
                     patch.object(R, 'ROOT', self.f.retired)):
            item.start(); self.addCleanup(item.stop)

    def test_closed_current_history_retains_original_enable_inodes_without_runtime_restart(self):
        links = self.d.boot_links(self.f.f.context); inodes = [x.lstat().st_ino for x in links]
        result = R.retire(self.c)
        self.assertTrue(result['originalRuntimeUnchanged'])
        self.assertEqual([x.lstat().st_ino for x in sorted((self.f.retired / 'links').iterdir())], inodes)
        self.assertEqual(self.f.states[self.d.RUNTIME]['MainPID'], '100')
        self.assertEqual(self.f.states[self.f.f.g.WATCHER]['MainPID'], '101')

    def test_pending_or_foreign_history_refuses_before_dependency_changes(self):
        (self.f.f.transactions / self.f.f.digest / 'rolled-back.json').unlink()
        with self.assertRaises(ValueError): R.retire(self.c)
        self.assertFalse(self.f.retired.exists())
        self.assertEqual(self.f.commands, [])

    def test_still_attached_dependency_refuses_before_stopping_old_barrier(self):
        self.f.requires = (self.d.BOOT + '\n').encode()
        with self.assertRaises(ValueError): R.retire(self.c)
        self.assertFalse(any(x[1] == 'stop' for x in self.f.commands))
        self.assertFalse((self.f.retired / 'complete.json').exists())

if __name__ == '__main__': unittest.main()
