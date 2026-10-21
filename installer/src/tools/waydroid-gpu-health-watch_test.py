# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import json
import hashlib
import tempfile

spec = importlib.util.spec_from_file_location('gpu_health_watch', Path(__file__).with_name('waydroid-gpu-health-watch.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class WatchTests(unittest.TestCase):
    def run_watch(self, stages, health=(), step=30):
        stages = iter(stages); health = iter(health); recovered = []; ticks = [0]; reports = []
        def sleep(_seconds):
            ticks[0] += step
        result = tool.watch(lambda: next(stages), lambda: next(health), lambda: recovered.append(True),
                            sleep=sleep, clock=lambda: ticks[0], report=reports.append)
        return result, recovered, reports

    def test_healthy_gpu_never_expires_for_user_absence(self):
        result, recovered, _ = self.run_watch(['ready'] * 5 + ['restored'], [True] * 5, step=86400)
        self.assertEqual(result, 'restored'); self.assertEqual(recovered, [])

    def test_three_bad_ready_observations_recover_once(self):
        result, recovered, _ = self.run_watch(['ready'] * 4, [True, False, False, False])
        self.assertEqual(result, 'recovered'); self.assertEqual(recovered, [True])

    def test_a_good_sample_resets_failure_streak(self):
        result, recovered, _ = self.run_watch(['ready'] * 6 + ['restored'], [True, False, False, True, False, False])
        self.assertEqual(result, 'restored'); self.assertEqual(recovered, [])

    def test_stuck_startup_recovers_without_waiting_for_user_feedback(self):
        result, recovered, _ = self.run_watch(['starting'] * 3, step=240)
        self.assertEqual(result, 'recovered'); self.assertEqual(recovered, [True])

    def test_prior_ready_status_allows_normal_reboot_startup_but_not_an_unbounded_hang(self):
        result, recovered, reports = self.run_watch(['ready'] * 3, [False, False, False], step=240)
        self.assertEqual(result, 'recovered'); self.assertEqual(recovered, [True])
        self.assertEqual(reports[-2]['failures'], 0)
        result, recovered, _ = self.run_watch(['ready'] * 4 + ['restored'], [False, False, False, True])
        self.assertEqual(result, 'restored'); self.assertEqual(recovered, [])

    def test_no_backup_before_apply_never_triggers_a_mutation(self):
        recovered = []; ticks = [0]
        def sleep(_seconds):
            ticks[0] += 60
        with self.assertRaisesRegex(RuntimeError, 'not started'):
            tool.watch(lambda: None, lambda: True, lambda: recovered.append(True),
                       sleep=sleep, clock=lambda: ticks[0], report=lambda _value: None)
        self.assertEqual(recovered, [])

    def test_read_error_does_not_mutate_unverified_backup(self):
        recovered = []
        def foreign():
            raise ValueError('Foreign metadata')
        with self.assertRaises(ValueError):
            tool.watch(foreign, lambda: False, lambda: recovered.append(True))
        self.assertEqual(recovered, [])

    def test_timeout_is_an_unhealthy_sample_not_a_watchdog_exit(self):
        engine = SimpleNamespace(trial=SimpleNamespace(run=Mock(side_effect=subprocess.TimeoutExpired('systemctl', 10))))
        self.assertFalse(tool.native_healthy(engine))

    def test_native_gpu_health_uses_services_and_allocator_not_a_video_picture_claim(self):
        properties = {'sys.boot_completed': '1', 'ro.hardware.gralloc': 'minigbm_gbm_mesa',
                      'init.svc.vendor.graphics.allocator-4-0-gbm_mesa': 'running'}
        for renderer, next_pid, expected in [('GLES: Mesa virgl', '123', True),
                                               ('GLES: ANGLE SwiftShader', '123', False),
                                               ('GLES: Mesa virgl', '124', False)]:
            run = Mock(side_effect=['\n'.join(['active'] * 8), *properties.values(), renderer])
            engine = SimpleNamespace(trial=SimpleNamespace(run=run),
                                     guest_identity=Mock(side_effect=[('123', None), (next_pid, None)]),
                                     EXPECTED_NATIVE=properties)
            self.assertEqual(tool.native_healthy(engine), expected)

    def test_status_cannot_authorize_foreign_or_changed_backup(self):
        with tempfile.TemporaryDirectory(prefix='ta-gpu-health-metadata-') as directory:
            root = Path(directory); original = b'owned-software'; candidate = b'gpu-' + original
            digest = lambda value: hashlib.sha256(value).hexdigest()
            (root / 'original.cfg').write_bytes(original)
            manifest = {'profile': 'fixed-u', 'before': digest(original), 'candidate': digest(candidate)}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            engine = SimpleNamespace(BACKUP=root, ORIGINAL=root / 'original.cfg', PROFILE='fixed-u',
                                     STAGES={'prepared', 'ready', 'restored'}, candidate=lambda value: b'gpu-' + value,
                                     trial=SimpleNamespace(protected=Mock(), digest=digest))
            self.assertEqual(tool.read_owned_stage(engine), 'prepared')
            status = {'profile': 'fixed-u', 'stage': 'ready'}
            (root / 'status.json').write_text(json.dumps(status))
            self.assertEqual(tool.read_owned_stage(engine), 'ready')
            for value in [{'profile': 'foreign', 'stage': 'ready'}, {'profile': 'fixed-u', 'stage': 'foreign'}]:
                (root / 'status.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    tool.read_owned_stage(engine)
            (root / 'status.json').write_text(json.dumps(status))
            (root / 'original.cfg').write_bytes(b'foreign')
            with self.assertRaises(ValueError):
                tool.read_owned_stage(engine)

    def test_guard_unit_survives_runtime_restart_without_a_ready_expiry(self):
        import configparser
        unit = configparser.ConfigParser(interpolation=None)
        unit.optionxform = str
        unit.read(Path(__file__).with_name('tesla-gpu-health-watch-20261011u.service'))
        self.assertEqual(unit['Service']['ExecStart'], '/usr/bin/python3 /opt/teslaandroid-waydroid/gpu-health-trial-20261011u/tools/waydroid-gpu-health-watch.py --watch')
        self.assertEqual(unit['Service']['Restart'], 'on-failure')
        self.assertEqual(unit['Service']['UMask'], '0077')
        self.assertNotIn('RuntimeMaxSec', unit['Service'])
        self.assertNotIn('BindsTo', unit['Unit'])
        self.assertNotIn('PartOf', unit['Unit'])
        self.assertEqual(unit['Install']['WantedBy'], 'multi-user.target')


if __name__ == '__main__':
    unittest.main()
