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

spec = importlib.util.spec_from_file_location('gpu_boot_repair', Path(__file__).with_name('waydroid-gpu-native-repair.py'))
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


class NativeHealth(unittest.TestCase):
    def sample(self, *, renderer='GLES: Mesa, virgl AMD Radeon', bad_property=None,
               graphics_active=True, groups='Groups: 44 990', mapper=True, generation='123'):
        properties = {
            'sys.boot_completed': '1', 'ro.hardware.gralloc': 'minigbm_gbm_mesa',
            'init.svc.vendor.graphics.allocator-4-0-gbm_mesa': 'running',
            'ro.waydroid.software_rendering': '0', 'debug.stagefright.ccodec': '4',
            'persist.waydroid.use_subsurface': 'true', 'media.sf.hwaccel': '0',
            'debug.ffmpeg-codec2.hwaccel.drm': '0', 'debug.ffmpeg-codec2.hwaccel.force': '0',
            'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
        if bad_property:
            properties[bad_property] = 'wrong'
        calls = []
        generations = iter(('123', generation))
        def run(args, _timeout):
            if args[0] == '/usr/bin/lxc-info':
                return next(generations)
            command = args[args.index('--') + 1:]
            if command[0].endswith('/getprop'):
                return properties[command[1]]
            if command[0].endswith('/dumpsys'):
                return renderer
            if command[0].endswith('/pidof'):
                return '456'
            if command[0].endswith('/cat'):
                return '/vendor/lib64/libminigbm_gralloc_gbm_mesa.so\ngralloc.minigbm_gbm_mesa.so' if mapper else ''
            raise AssertionError('Unexpected native probe')
        def service(*args):
            calls.append(args)
            if args[0] == 'is-active':
                return '\n'.join(['active' if graphics_active else 'inactive'] * 3)
            return '789'
        trial = types.SimpleNamespace(run=run, service=service, COMPOSITOR='compositor', SESSION='session')
        with mock.patch.object(Path, 'read_text', return_value=groups), \
                mock.patch.object(repair, 'pinned'), mock.patch.object(repair, 'report'):
            result = repair.healthy(trial)
        return result, calls

    def test_idle_touch_and_absent_streaming_peers_do_not_disable_gpu(self):
        result, calls = self.sample()
        self.assertTrue(result)
        self.assertEqual(calls[0], ('is-active', 'compositor', 'session', 'waydroid-container.service'))
        self.assertFalse(any('preview.service' in value for call in calls for value in call))

    def test_native_graphics_and_decode_invariants_still_fail_closed(self):
        failures = [dict(renderer='GLES: ANGLE SwiftShader'),
                    dict(renderer='GLES: virgl llvmpipe'), dict(renderer='GLES: virgl softpipe'),
                    dict(bad_property='debug.stagefright.ccodec'),
                    dict(bad_property='ro.waydroid.software_rendering'),
                    dict(bad_property='init.svc.vendor.graphics.allocator-4-0-gbm_mesa'),
                    dict(graphics_active=False), dict(groups='Groups: 44'),
                    dict(mapper=False), dict(generation='124')]
        for failure in failures:
            with self.subTest(failure=failure):
                self.assertFalse(self.sample(**failure)[0])

    def test_new_transaction_never_reuses_predecessor_outputs(self):
        for value in (repair.ROOT, repair.BACKUP, repair.DROP, repair.GUARD):
            self.assertIn('gpu-native-repair-20261024hr', str(value))
        self.assertIn(b'waydroid-gpu-native-repair.py --watch', repair.UNIT_CODE)
        self.assertNotIn(b'RuntimeMaxSec', repair.UNIT_CODE)


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
