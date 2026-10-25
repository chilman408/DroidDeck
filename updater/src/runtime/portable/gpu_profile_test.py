# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
ORIGINAL = b'[waydroid]\narch = x86_64\nimages_path = /etc/waydroid-extra/images\n\n[properties]\n\n'
sha = lambda value: hashlib.sha256(value).hexdigest()


class GpuCliTests(unittest.TestCase):
    def test_closed_cli_config_and_existing_backup_paths_without_start_or_regeneration(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_switch_gpu_profile') as switch:
            for option, restore in [('--waydroid-gpu-profile', False), ('--restore-waydroid-gpu-profile', True)]:
                P.main([option])
                self.assertEqual(switch.call_args.args[1:],
                    (Path('/var/lib/waydroid/waydroid.cfg'), Path('/var/lib/droiddeck/waydroid-profile')))
                self.assertEqual(switch.call_args.kwargs, {'restore': restore})
                with self.assertRaises(ValueError):
                    P.main([option, '/arbitrary-path'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class GpuProfileTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-gpu-profile-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'backup'
        self.backup.mkdir(mode=0o700)
        for name, data in {'original.cfg': ORIGINAL, 'baseline.cfg': P.profile_candidate(ORIGINAL),
                           'gpu.cfg': P.profile_candidate(ORIGINAL, gpu=True)}.items():
            path = self.backup / name
            path.write_bytes(data)
            path.chmod(0o600)
        self.baseline = P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL))
        self.gpu = P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL, gpu=True))
        self.cfg = self.root / 'waydroid.cfg'
        self.cfg.write_bytes(self.baseline)
        self.cfg.chmod(0o644)

    def switch(self, *, restore=False, guard=lambda: None, verify=lambda _: None):
        return P._switch_gpu_profile(self.copier, self.cfg, self.backup, original_pin=sha(ORIGINAL),
                                     restore=restore, guard=guard, verify_codecs=verify)

    def test_apply_restore_retains_both_displaced_configs_and_no_activation(self):
        before_backup = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()}
        result = self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)
        for key in ('servicesActivated', 'androidOperationsExecuted', 'propertiesRegenerated',
                    'savedPropertiesChanged', 'allocatorOverrideChanged', 'independentRecoveryArmed', 'readyForActivation'):
            self.assertFalse(result[key])
        self.assertEqual(result['graphicsProfile'], 'gpu')
        result = self.switch(restore=True)
        self.assertEqual(self.cfg.read_bytes(), self.baseline)
        self.assertEqual((self.root / 'pending-droiddeck-gpu-restore.cfg').read_bytes(), self.gpu)
        self.assertEqual(result['graphicsProfile'], 'software')
        self.assertEqual(before_backup, {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()})
        self.assertEqual((self.cfg.stat().st_uid, self.cfg.stat().st_gid, self.cfg.stat().st_mode & 0o777,
                          self.cfg.stat().st_nlink), (0, 0, 0o644, 1))

    def test_apply_and_restore_replay_refused(self):
        self.switch()
        with self.assertRaises(ValueError):
            self.switch()
        self.switch(restore=True)
        with self.assertRaises(ValueError):
            self.switch(restore=True)
        with self.assertRaises(FileExistsError):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_foreign_config_or_unknown_protocol_refused_without_exchange(self):
        for value in (b'foreign', self.baseline.replace(b'aidl3', b'aidl2')):
            self.cfg.write_bytes(value)
            with self.assertRaises(ValueError):
                self.switch()
            self.assertEqual(self.cfg.read_bytes(), value)
            self.assertFalse((self.root / 'pending-droiddeck-gpu.cfg').exists())

    def test_corrupted_extra_or_linked_backup_refused(self):
        source = self.backup / 'gpu.cfg'
        source.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.switch()
        source.write_bytes(P.profile_candidate(ORIGINAL, gpu=True))
        (self.backup / 'extra').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.switch()
        (self.backup / 'extra').unlink()
        other = self.root / 'linked'
        source.rename(other)
        source.symlink_to(other)
        with self.assertRaises(OSError):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_restore_requires_exact_owned_displaced_baseline(self):
        self.cfg.write_bytes(self.gpu)
        with self.assertRaises(FileNotFoundError):
            self.switch(restore=True)
        displaced = self.root / 'pending-droiddeck-gpu.cfg'
        displaced.write_bytes(b'foreign')
        displaced.chmod(0o644)
        with self.assertRaises(ValueError):
            self.switch(restore=True)
        self.assertEqual(displaced.read_bytes(), b'foreign')
        self.assertEqual(self.cfg.read_bytes(), self.gpu)

    def test_foreign_pending_file_is_never_overwritten(self):
        pending = self.root / 'pending-droiddeck-gpu.cfg'
        pending.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError):
            self.switch()
        self.assertEqual(pending.read_bytes(), b'foreign')
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_active_guard_or_codec_failure_before_prepare_leaves_no_pending(self):
        def fail(*_):
            raise ValueError('generated refusal')
        for options in ({'guard': fail}, {'verify': fail}):
            with self.assertRaises(ValueError):
                self.switch(**options)
            self.assertEqual(self.cfg.read_bytes(), self.baseline)
            self.assertFalse((self.root / 'pending-droiddeck-gpu.cfg').exists())

    def test_last_guard_race_refuses_exchange(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2:
                self.cfg.write_bytes(b'foreign-race')
        with self.assertRaises(ValueError):
            self.switch(guard=guard)
        self.assertEqual(self.cfg.read_bytes(), b'foreign-race')

    def test_postexchange_active_guard_reports_uncertainty_with_both_configs(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 3:
                raise ValueError('generated activation race')
        with self.assertRaises(P.PromotionUncertain):
            self.switch(guard=guard)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)

    def test_codec_change_during_check_is_refused_before_exchange(self):
        count = 0
        def verify(_):
            nonlocal count
            count += 1
            if count == 3:
                raise ValueError('generated codec change')
        with self.assertRaises(ValueError):
            self.switch(verify=verify)
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_postexchange_sync_failure_reports_uncertainty_with_both_configs(self):
        sync = os.fsync
        def fail(fd):
            if self.cfg.read_bytes() == self.gpu:
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)


if __name__ == '__main__':
    unittest.main()
