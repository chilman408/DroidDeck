# SPDX-License-Identifier: GPL-3.0-only
import configparser
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('gpu_boot_repair', Path(__file__).with_name('waydroid-gpu-boot-repair.py'))
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class Recipe(unittest.TestCase):
    def test_startup_wait_retains_full_health_predicates(self):
        ticks = iter((0, 0, 2, 4))
        sleep = mock.Mock()
        with mock.patch.object(repair, 'healthy', side_effect=(False, False, True)) as check:
            self.assertTrue(repair.wait_healthy(None, clock=lambda: next(ticks), sleep=sleep))
            self.assertEqual(check.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_startup_wait_is_bounded(self):
        ticks = iter((0, 0, 119, 120))
        with mock.patch.object(repair, 'healthy', return_value=False) as check:
            self.assertFalse(repair.wait_healthy(None, clock=lambda: next(ticks), sleep=lambda _seconds: None))
            self.assertEqual(check.call_count, 2)

    def test_service_only_groups_and_fixed_gl_command(self):
        self.assertIn(b'SupplementaryGroups=render video\n', repair.DROP_CODE)
        self.assertIn(b'--renderer=gl ', repair.DROP_CODE)
        self.assertNotIn(b'User=root', repair.DROP_CODE)
        self.assertNotIn(b'RuntimeMaxSec', repair.UNIT_CODE)
        self.assertNotIn(b'BindsTo', repair.UNIT_CODE)
        self.assertNotIn(b'PartOf', repair.UNIT_CODE)

    def test_early_decode_guard_matches_accepted_bytes(self):
        self.assertEqual(repair.digest(repair.INIT_CODE), '3b7d620cd8cf17b6450c744f95ecaf373a5fa39391f5b25da6ef7d000b26a286')
        self.assertEqual(repair.CODE, b'ro.hardware.gralloc=minigbm_gbm_mesa\n')

    def test_all_independent_pins_are_sha256(self):
        for sha in [repair.BASE, repair.GPU, repair.AAC_SHA, *repair.PINS.values(), *repair.HELPERS.values()]:
            self.assertRegex(sha, r'^[0-9a-f]{64}$')

    def test_candidate_changes_only_three_graphics_properties(self):
        raw = (b'[waydroid]\narch = x86_64\n\n[properties]\n'
               b'ro.waydroid.software_rendering = 1\n'
               b'persist.waydroid.use_subsurface = false\n'
               b'debug.stagefright.ccodec = 4\nfixture.untouched = value\n\n')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(raw.decode())
        parser.set('properties', 'ro.waydroid.software_rendering', '0')
        parser.set('properties', 'ro.hardware.gralloc', 'minigbm_gbm_mesa')
        parser.set('properties', 'persist.waydroid.use_subsurface', 'true')
        stream = io.StringIO()
        parser.write(stream)
        expected = stream.getvalue().encode()
        with mock.patch.object(repair, 'BASE', repair.digest(raw)), mock.patch.object(repair, 'GPU', repair.digest(expected)):
            self.assertEqual(repair.candidate(raw), expected)
            with self.assertRaises(RuntimeError):
                repair.candidate(raw + b'\n')
        with mock.patch.object(repair, 'BASE', repair.digest(raw)):
            with self.assertRaises(RuntimeError):
                repair.candidate(raw)

    def test_unknown_cli_never_loads_helper(self):
        with mock.patch.object(repair, 'load') as load:
            for args in ([], ['--retry'], ['--apply', 'other-host']):
                with self.assertRaises(RuntimeError):
                    repair.main(args)
            load.assert_not_called()

    def test_previous_or_link_output_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'generated'
            repair.absent(path)
            path.write_bytes(b'foreign')
            with self.assertRaises(RuntimeError):
                repair.absent(path)
            self.assertEqual(path.read_bytes(), b'foreign')


class Transaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-gpu-repair-test-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.events = []
        for name, path in {'BACKUP': root / 'backup', 'CFG': root / 'live.cfg',
                           'UNIT': root / 'guard.service', 'INIT': root / 'init.rc',
                           'DROP': root / 'compositor.conf'}.items():
            patch = mock.patch.object(repair, name, path)
            patch.start()
            self.addCleanup(patch.stop)
        repair.CFG.write_bytes(b'generated-config')
        def save(path, raw, _mode):
            self.events.append(('save', path.name))
            path.write_bytes(raw)
        def service(*args):
            self.events.append(('service', *args))
            return 'active'
        self.trial = types.SimpleNamespace(save=save, service=service,
            stop=lambda: self.events.append('stop'), start=lambda: self.events.append('start'),
            ready=lambda: self.events.append('ready'))
        self.property = mock.Mock()
        self.property.set_value.side_effect = lambda *args: self.events.append(('property', *args))
        for name, value in {'baseline': lambda *_args: self.events.append('baseline'),
                            'store': lambda *_args: self.property,
                            'override': lambda **_args: self.events.append('override'),
                            'candidate': lambda _raw: b'generated-gpu-config',
                            'pinned': lambda *_args: self.events.append('pinned')}.items():
            patch = mock.patch.object(repair, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_guard_armed_before_any_live_graphics_mutation(self):
        with mock.patch.object(repair, 'healthy', return_value=True), mock.patch.object(repair, 'report'):
            repair.apply(self.trial, None)
        armed = ('service', 'enable', '--now', repair.GUARD)
        self.assertLess(self.events.index(armed), self.events.index('stop'))
        self.assertLess(self.events.index('stop'), self.events.index(('property', ('false',), 'true')))
        self.assertLess(self.events.index(('save', 'init.rc')), self.events.index('start'))
        self.assertLess(self.events.index(('save', 'compositor.conf')), self.events.index('start'))
        self.assertEqual(json.loads((repair.BACKUP / 'status.json').read_bytes())['stage'], 'ready')
        self.assertEqual((repair.BACKUP / 'software.cfg').read_bytes(), b'generated-config')

    def test_native_failure_restores_without_success(self):
        with mock.patch.object(repair, 'wait_healthy', return_value=False), mock.patch.object(repair, 'restore') as restore:
            with self.assertRaises(RuntimeError):
                repair.apply(self.trial, None)
            restore.assert_called_once_with(self.trial, None)

    def test_start_failure_restores(self):
        self.trial.start = mock.Mock(side_effect=RuntimeError('generated failure'))
        with mock.patch.object(repair, 'restore') as restore:
            with self.assertRaises(RuntimeError):
                repair.apply(self.trial, None)
            restore.assert_called_once()

    def test_unarmed_guard_leaves_live_files_untouched(self):
        self.trial.service = mock.Mock(return_value='inactive')
        with mock.patch.object(repair, 'restore') as restore:
            with self.assertRaises(RuntimeError):
                repair.apply(self.trial, None)
            restore.assert_not_called()
        self.assertNotIn('stop', self.events)
        self.assertFalse(repair.INIT.exists())
        self.assertFalse(repair.DROP.exists())
        self.assertEqual(repair.CFG.read_bytes(), b'generated-config')

    def test_preflight_failure_creates_nothing(self):
        with mock.patch.object(repair, 'baseline', side_effect=RuntimeError('fixture drift')):
            with self.assertRaises(RuntimeError):
                repair.apply(self.trial, None)
        self.assertFalse(repair.BACKUP.exists())
        self.assertEqual(self.events, [])


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root fixture ownership required')
class NativeOverride(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-gpu-repair-fixtures-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        misc = Path(self.temp.name) / 'misc'
        misc.mkdir(mode=0o700)
        os.chown(misc, 1000, 9998)
        misc.chmod(0o1771)
        for name, value in (('MISC', misc), ('OVERRIDE', misc / 'waydroid_settings')):
            patch = mock.patch.object(repair, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_generated_override_root_private_and_removable(self):
        repair.override()
        info = repair.OVERRIDE.lstat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)), (0, 0, 1, 0o600))
        self.assertEqual(repair.OVERRIDE.read_bytes(), repair.CODE)
        repair.override(removing=True)
        self.assertFalse(repair.OVERRIDE.exists())

    def test_existing_foreign_override_never_replaced_or_removed(self):
        repair.OVERRIDE.write_bytes(b'foreign')
        repair.OVERRIDE.chmod(0o600)
        with self.assertRaises(FileExistsError):
            repair.override()
        with self.assertRaises(RuntimeError):
            repair.override(removing=True)
        self.assertEqual(repair.OVERRIDE.read_bytes(), b'foreign')

    def test_symlink_refused(self):
        target = Path(self.temp.name) / 'foreign'
        target.write_bytes(repair.CODE)
        target.chmod(0o600)
        repair.OVERRIDE.symlink_to(target)
        with self.assertRaises(OSError):
            repair.override(removing=True)
        self.assertTrue(repair.OVERRIDE.is_symlink())

    def test_wrong_misc_ownership_refused(self):
        os.chown(repair.MISC, 0, 0)
        with self.assertRaises(RuntimeError):
            repair.override()
        self.assertFalse(repair.OVERRIDE.exists())


if __name__ == '__main__':
    unittest.main()
