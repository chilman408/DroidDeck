# SPDX-License-Identifier: GPL-3.0-only
import configparser
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
ORIGINAL = b'[waydroid]\narch = x86_64\nimages_path = /etc/waydroid-extra/images\nsystem_ota = None\nvendor_ota = None\n\n[properties]\n\n'
sha = lambda b: hashlib.sha256(b).hexdigest()


class ProtocolPolicyTests(unittest.TestCase):
    def test_fixed_cli_refuses_overrides_before_loading_copier(self):
        with mock.patch.object(P, 'load_copier', side_effect=AssertionError('no copier')):
            with self.assertRaises(ValueError):
                P.main(['--waydroid-protocol-baseline', '/foreign'])


class ProfilePolicyTests(unittest.TestCase):
    def test_closed_baseline_gpu_plan_preserves_image_identity_and_codec4(self):
        for gpu in (False, True):
            p = configparser.ConfigParser(interpolation=None)
            p.read_string(P.profile_candidate(ORIGINAL, gpu=gpu).decode())
            before = configparser.ConfigParser(interpolation=None)
            before.read_string(ORIGINAL.decode())
            self.assertEqual(dict(p.items('waydroid')), dict(before.items('waydroid')))
            self.assertEqual(dict(p.items('properties')), {'debug.stagefright.ccodec': '4',
                'ro.waydroid.software_rendering': '0' if gpu else '1',
                'persist.waydroid.use_subsurface': 'true' if gpu else 'false',
                'media.sf.hwaccel': '0', 'debug.ffmpeg-codec2.hwaccel.drm': '0',
                'debug.ffmpeg-codec2.hwaccel.force': '0', 'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'})

    def test_nonfresh_config_and_duplicate_or_private_sections_reject(self):
        for data in (b'x', ORIGINAL + b'key=value\n', ORIGINAL.replace(b'x86_64', b'arm64'),
                     ORIGINAL + b'[accounts]\nsecret=x\n', b'x' * 65537,
                     ORIGINAL + b'[properties]\n', ORIGINAL + b'[DEFAULT]\nkey=value\n'):
            with self.assertRaises(ValueError):
                P.profile_candidate(data)

    def test_cli_fixed_backup_and_config_paths_without_activation(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_configure_waydroid') as apply:
            P.main(['--waydroid-baseline'])
            self.assertEqual(apply.call_args.args[1:], (Path('/var/lib/waydroid/waydroid.cfg'), Path('/var/lib/droiddeck')))
            self.assertFalse(apply.call_args.kwargs['restore'])
            P.main(['--restore-waydroid-baseline'])
            self.assertTrue(apply.call_args.kwargs['restore'])
            with self.assertRaises(ValueError):
                P.main(['--waydroid-baseline', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class ProfileInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-profile-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = self.root / 'waydroid.cfg'
        self.cfg.write_bytes(ORIGINAL)
        self.cfg.chmod(0o644)
        self.backups = self.root / 'backups'
        self.backups.mkdir(mode=0o700)

    def apply(self, **kw):
        return P._configure_waydroid(self.copier, self.cfg, self.backups, original_pin=sha(ORIGINAL),
                    guard=kw.get('guard', lambda: None), verify_codecs=lambda _: None, restore=kw.get('restore', False))

    def test_apply_exact_backup_and_displaced_original_then_restore_without_data_changes(self):
        r = self.apply()
        baseline = P.profile_candidate(ORIGINAL)
        self.assertEqual(self.cfg.read_bytes(), baseline)
        self.assertFalse(r['androidOperationsExecuted'])
        self.assertFalse(r['servicesActivated'])
        backup = self.backups / 'waydroid-profile'
        self.assertEqual(set(p.name for p in backup.iterdir()), {'original.cfg', 'baseline.cfg', 'gpu.cfg'})
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        for p in backup.iterdir():
            self.assertEqual((p.stat().st_uid, p.stat().st_gid, p.stat().st_mode & 0o777, p.stat().st_nlink), (0, 0, 0o600, 1))
        self.assertEqual((backup / 'original.cfg').read_bytes(), ORIGINAL)
        self.assertEqual((self.root / 'pending-droiddeck-baseline.cfg').read_bytes(), ORIGINAL)
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o644)
        self.assertNotEqual(self.cfg.stat().st_ino, (backup / 'baseline.cfg').stat().st_ino)
        with self.assertRaises(ValueError):
            self.apply()
        self.apply(restore=True)
        self.assertEqual(self.cfg.read_bytes(), ORIGINAL)
        self.assertEqual((self.root / 'pending-droiddeck-restore.cfg').read_bytes(), baseline)
        with self.assertRaises(ValueError):
            self.apply(restore=True)

    def test_changed_current_configuration_refuses_before_backup(self):
        self.cfg.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.cfg.read_bytes(), b'foreign')
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_restore_accepts_only_exact_known_aidl3_boot_augmentation(self):
        self.apply()
        self.cfg.write_bytes(P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL)))
        self.apply(restore=True)
        self.assertEqual(self.cfg.read_bytes(), ORIGINAL)

    def test_restore_refuses_unknown_protocol_or_other_boot_mutation(self):
        self.apply()
        self.cfg.write_bytes(P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL)).replace(b'aidl3', b'foreign'))
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.assertIn(b'foreign', self.cfg.read_bytes())

    def test_corrupted_or_extra_backup_refuses_restore(self):
        self.apply()
        backup = self.backups / 'waydroid-profile'
        (backup / 'gpu.cfg').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.assertEqual(self.cfg.read_bytes(), P.profile_candidate(ORIGINAL))

    def test_linked_original_configuration_refuses_before_backup(self):
        real = self.root / 'real.cfg'
        self.cfg.rename(real)
        self.cfg.symlink_to(real)
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_guard_failure_after_backup_retains_original_and_no_exchange(self):
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError('generated active service')
        with self.assertRaises(ValueError):
            self.apply(guard=guard)
        self.assertEqual(self.cfg.read_bytes(), ORIGINAL)
        self.assertEqual((self.backups / 'waydroid-profile/original.cfg').read_bytes(), ORIGINAL)

    def test_pending_foreign_file_is_never_overwritten(self):
        (self.root / 'pending-droiddeck-baseline.cfg').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(self.cfg.read_bytes(), ORIGINAL)
        self.assertEqual((self.root / 'pending-droiddeck-baseline.cfg').read_bytes(), b'preserve')

    def test_last_guard_configuration_mutation_refuses_exchange(self):
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.cfg.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.apply(guard=guard)
        self.assertEqual(self.cfg.read_bytes(), b'changed')
        self.assertEqual((self.backups / 'waydroid-profile/original.cfg').read_bytes(), ORIGINAL)

    def test_postexchange_sync_failure_reports_uncertain_retains_both_configurations(self):
        sync = os.fsync
        def fail(fd):
            if self.cfg.read_bytes() == P.profile_candidate(ORIGINAL):
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.apply()
        self.assertEqual(self.cfg.read_bytes(), P.profile_candidate(ORIGINAL))
        self.assertEqual((self.root / 'pending-droiddeck-baseline.cfg').read_bytes(), ORIGINAL)
        self.assertEqual((self.backups / 'waydroid-profile/original.cfg').read_bytes(), ORIGINAL)

    def test_race_at_exchange_keeps_foreign_displaced_file_and_original_backup(self):
        library = P.ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
        rename.argtypes = (P.ctypes.c_int, P.ctypes.c_char_p, P.ctypes.c_int, P.ctypes.c_char_p, P.ctypes.c_uint)
        rename.restype = P.ctypes.c_int
        def race(*args):
            foreign = self.root / 'foreign.cfg'
            foreign.write_bytes(b'foreign root change')
            foreign.chmod(0o644)
            os.replace(foreign, self.cfg)
            return rename(*args)
        # Backup publication uses no-replace before this exchange-only wrapper.
        real_exclusive = P.rename_exclusive
        with mock.patch.object(P, 'rename_exclusive', side_effect=real_exclusive):
            original_exchange = P._exchange_profile
            def exchange(*args, **kwargs):
                with mock.patch.object(P.ctypes, 'CDLL', return_value=mock.Mock(renameat2=race)):
                    return original_exchange(*args, **kwargs)
            with mock.patch.object(P, '_exchange_profile', side_effect=exchange), self.assertRaises(P.PromotionUncertain):
                self.apply()
        self.assertEqual((self.root / 'pending-droiddeck-baseline.cfg').read_bytes(), b'foreign root change')
        self.assertEqual((self.backups / 'waydroid-profile/original.cfg').read_bytes(), ORIGINAL)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class ProtocolPreparationTests(unittest.TestCase):
    setUp = ProfileInstallTests.setUp

    def prepare(self, **kwargs):
        return P._prepare_profile_protocols(self.copier, self.cfg, self.backups / 'waydroid-profile',
            original_pin=sha(ORIGINAL), guard=kwargs.get('guard', lambda: None), verify_codecs=lambda _: None)

    def baseline(self):
        P._configure_waydroid(self.copier, self.cfg, self.backups, original_pin=sha(ORIGINAL),
                             guard=lambda: None, verify_codecs=lambda _: None)

    def test_first_session_preparation_preserves_sources_and_displaced_original(self):
        self.baseline()
        baseline, before = self.cfg.read_bytes(), self.cfg.stat().st_ino
        result = self.prepare()
        self.assertTrue(result['protocolBaselinePrepared'])
        self.assertFalse(result['servicesActivated'])
        self.assertFalse(result['androidOperationsExecuted'])
        self.assertEqual(self.cfg.read_bytes(), P.profile_with_detected_protocols(baseline))
        displaced = self.root / 'pending-droiddeck-protocols.cfg'
        self.assertEqual((displaced.read_bytes(), displaced.stat().st_ino), (baseline, before))
        self.assertEqual((self.backups / 'waydroid-profile/baseline.cfg').read_bytes(), baseline)
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o644)
        with self.assertRaises(ValueError): self.prepare()

    def test_unknown_current_or_already_normalized_profile_is_not_replaced(self):
        self.baseline()
        baseline = self.cfg.read_bytes()
        for current in (b'foreign', P.profile_with_detected_protocols(baseline),
                        P.profile_with_detected_protocols(baseline).replace(b'aidl3', b'hidl')):
            self.cfg.write_bytes(current)
            with self.assertRaises(ValueError): self.prepare()
            self.assertEqual(self.cfg.read_bytes(), current)
            self.assertFalse((self.root / 'pending-droiddeck-protocols.cfg').exists())

    def test_existing_pending_or_extra_profile_source_refuses(self):
        self.baseline()
        pending = self.root / 'pending-droiddeck-protocols.cfg'
        pending.write_bytes(b'preserved')
        with self.assertRaises(OSError): self.prepare()
        self.assertEqual(pending.read_bytes(), b'preserved')
        pending.unlink()
        (self.backups / 'waydroid-profile/foreign').write_bytes(b'generated')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(pending.exists())

    def test_linked_or_corrupt_backup_refuses_before_pending_file(self):
        self.baseline()
        source = self.backups / 'waydroid-profile/gpu.cfg'
        raw = source.read_bytes()
        source.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.prepare()
        source.unlink()
        source.symlink_to('baseline.cfg')
        with self.assertRaises(OSError): self.prepare()
        self.assertFalse((self.root / 'pending-droiddeck-protocols.cfg').exists())

    def test_active_worker_guard_refuses_before_write(self):
        self.baseline()
        before = self.cfg.read_bytes()
        def active(): raise ValueError('generated active worker')
        with self.assertRaises(ValueError): self.prepare(guard=active)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertFalse((self.root / 'pending-droiddeck-protocols.cfg').exists())

    def test_source_drift_after_staging_preserves_original_and_pending(self):
        self.baseline()
        before, calls = self.cfg.read_bytes(), 0
        def drift():
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.backups / 'waydroid-profile/gpu.cfg').write_bytes(b'changed')
        with self.assertRaises(P.PromotionUncertain): self.prepare(guard=drift)
        self.assertTrue((self.root / 'pending-droiddeck-protocols.cfg').exists())
        # The existing exchange primitive preserves both generations after late drift.
        self.assertIn(before, (self.cfg.read_bytes(), (self.root / 'pending-droiddeck-protocols.cfg').read_bytes()))


if __name__ == '__main__':
    unittest.main()
