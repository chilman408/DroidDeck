# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('machine_gpu_promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
ORIGINAL = b'[waydroid]\narch = x86_64\nimages_path = /etc/waydroid-extra/images\nvendor_type = VANILLA\n\n[properties]\n\n'
CONFIG = {'version': 1, 'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman',
          'lanAddress': '192.168.2.253', 'port': 9443, 'hostname': 'device.teslaandroid.com'}
sha = lambda data: hashlib.sha256(data).hexdigest()


class MachineGpuRecipeTests(unittest.TestCase):
    def test_derives_profiles_from_each_fresh_original_not_single_vm_pin(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_profiles', None)), 'Machine GPU profile recipe missing')
        first = P._machine_gpu_profiles(ORIGINAL)
        self.assertEqual(first['originalConfigurationSha256'], sha(ORIGINAL))
        # Compare against accepted existing profile generators, not the new policy builder.
        self.assertEqual(first['softwareConfigurationSha256'], sha(P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL))))
        self.assertEqual(first['gpuConfigurationSha256'], sha(P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL, gpu=True))))
        other = P._machine_gpu_profiles(ORIGINAL.replace(b'VANILLA', b'GAPPS'))
        self.assertNotEqual(first, other)
        self.assertNotEqual(first['originalConfigurationSha256'], P.WAYDROID_CONFIGURATION_SHA256)
        for invalid in (b'', b'foreign', ORIGINAL.replace(b'x86_64', b'arm64'),
                        ORIGINAL.replace(b'[properties]\n', b'[properties]\nprivate = generated\n')):
            with self.assertRaises(ValueError):
                P._machine_gpu_profiles(invalid)

    def test_closed_cli_destinations_and_no_force_or_hash_overrides(self):
        self.assertTrue(callable(getattr(P, '_switch_machine_gpu_profile', None)), 'Machine GPU transition missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_switch_machine_gpu_profile') as switch, \
                mock.patch.object(P, '_machine_gpu_policy') as inspect:
            for option, restore in [('--machine-waydroid-gpu-profile', False), ('--restore-machine-waydroid-gpu-profile', True)]:
                P.main([option])
                self.assertEqual(switch.call_args.args, (copier.return_value, Path('/var/lib/waydroid/waydroid.cfg'),
                                                        Path('/var/lib/droiddeck/waydroid-profile'), Path('/etc/droiddeck')))
                self.assertEqual(switch.call_args.kwargs, {'restore': restore})
                with self.assertRaises(ValueError):
                    P.main([option, '--force'])
            P.main(['--machine-gpu-profile-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            with self.assertRaises(ValueError):
                P.main(['--machine-gpu-profile-policy', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux protected fixtures required')
class MachineGpuTransitions(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='dd-machine-gpu-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'backup'
        self.backup.mkdir(mode=0o700)
        for name, data in {'original.cfg': ORIGINAL, 'baseline.cfg': P.profile_candidate(ORIGINAL),
                           'gpu.cfg': P.profile_candidate(ORIGINAL, gpu=True)}.items():
            filename = self.backup / name
            filename.write_bytes(data)
            filename.chmod(0o600)
        self.baseline = P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL))
        self.gpu = P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL, gpu=True))
        self.cfg = self.root / 'waydroid.cfg'
        self.cfg.write_bytes(self.baseline)
        self.cfg.chmod(0o644)
        self.metadata_root = self.root / 'etc-droiddeck'
        self.metadata_root.mkdir(mode=0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.metadata.write_text(json.dumps(CONFIG, indent=2) + '\n')
        self.metadata.chmod(0o644)

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), CONFIG)
        return {'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64}

    def switch(self, *, restore=False, inactive=None, real=False, verify=None):
        return P._switch_machine_gpu_profile(self.copier, self.cfg, self.backup, self.metadata_root, restore=restore,
                                             validate=None if real else self.validate, inactive=inactive or (lambda: None),
                                             verify_codecs=verify or (lambda _: None))

    def test_apply_and_owned_restore_preserve_sources_and_all_nonrendering_contracts(self):
        original = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()}
        for restore in (False, True):
            receipt = self.switch(restore=restore)
            self.assertEqual(self.cfg.read_bytes(), self.baseline if restore else self.gpu)
            self.assertTrue(receipt['gpuProfilePolicyRegenerated'])
            self.assertEqual(receipt['originalConfigurationSha256'], sha(ORIGINAL))
            self.assertFalse(receipt['accountValidated'])
            for flag in ('servicesActivated', 'propertiesRegenerated', 'savedPropertiesChanged', 'allocatorOverrideChanged',
                         'independentRecoveryArmed', 'androidOperationsExecuted', 'readyForActivation', 'installable'):
                self.assertFalse(receipt[flag])
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)
        self.assertEqual((self.root / 'pending-droiddeck-gpu-restore.cfg').read_bytes(), self.gpu)
        self.assertEqual(original, {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()})

    def test_real_nss_metadata_is_admitted_but_wrong_uid_is_not(self):
        self.assertTrue(self.switch(real=True)['accountValidated'])
        self.metadata.write_text(json.dumps({**CONFIG, 'uid': 11000}))
        with self.assertRaises(ValueError):
            self.switch(real=True, restore=True)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)

    def test_readonly_policy_needs_no_service_downtime_and_contains_no_raw_profiles(self):
        with mock.patch.object(P, '_assert_codec_stopped', side_effect=AssertionError('readonly must not stop/require idle')):
            receipt = P._machine_gpu_policy(self.copier, self.metadata_root, self.backup,
                                            validate=self.validate, verify_codecs=lambda _: None)
        self.assertFalse(receipt['codecsVerified'])
        self.assertNotIn('VANILLA', json.dumps(receipt))
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_busy_or_codec_refusal_creates_no_pending_file(self):
        def deny(*_):
            raise ValueError('generated refusal')
        for kwargs in ({'inactive': deny}, {'verify': deny}):
            with self.assertRaises(ValueError):
                self.switch(**kwargs)
            self.assertFalse((self.root / 'pending-droiddeck-gpu.cfg').exists())
            self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_foreign_source_inventory_or_modified_gpu_backup_refuses(self):
        filename = self.backup / 'gpu.cfg'
        data = filename.read_bytes()
        filename.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.switch()
        filename.write_bytes(data)
        (self.backup / 'extra').write_bytes(b'generated')
        with self.assertRaises(ValueError):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_linked_metadata_and_source_are_never_admitted(self):
        link = self.root / 'linked'
        for filename in (self.metadata, self.backup / 'original.cfg'):
            os.link(filename, link)
            with self.assertRaises(ValueError):
                self.switch()
            link.unlink()
        (self.backup / 'original.cfg').rename(link)
        (self.backup / 'original.cfg').symlink_to(link)
        with self.assertRaises(OSError):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_metadata_mutation_while_candidate_written_prevents_exchange(self):
        write = self.copier.write_all
        def mutate(fd, data):
            result = write(fd, data)
            self.metadata.write_bytes(b'generated changed metadata')
            return result
        with mock.patch.object(self.copier, 'write_all', side_effect=mutate), self.assertRaises(ValueError):
            self.switch()
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_postexchange_metadata_mutation_is_uncertain_and_retains_displaced_config(self):
        def mutate():
            if self.cfg.read_bytes() == self.gpu:
                self.metadata.write_bytes(b'generated changed metadata')
        with self.assertRaises(P.PromotionUncertain):
            self.switch(inactive=mutate)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)

    def test_postexchange_inactive_check_timeout_is_uncertain_not_retryable_failure(self):
        def timed_out():
            if self.cfg.read_bytes() == self.gpu:
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.switch(inactive=timed_out)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual((self.root / 'pending-droiddeck-gpu.cfg').read_bytes(), self.baseline)

    def test_preexchange_timeout_is_refused_without_publication(self):
        def timed_out():
            raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.switch(inactive=timed_out)
        self.assertEqual(self.cfg.read_bytes(), self.baseline)
        self.assertFalse((self.root / 'pending-droiddeck-gpu.cfg').exists())

    def test_postrestore_timeout_is_uncertain_and_preserves_displaced_gpu(self):
        self.switch()
        def timed_out():
            if self.cfg.read_bytes() == self.baseline:
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.switch(restore=True, inactive=timed_out)
        self.assertEqual(self.cfg.read_bytes(), self.baseline)
        self.assertEqual((self.root / 'pending-droiddeck-gpu-restore.cfg').read_bytes(), self.gpu)

    def test_replay_and_unowned_restore_are_refused_without_overwrite(self):
        self.switch()
        with self.assertRaises(ValueError):
            self.switch()
        displaced = self.root / 'pending-droiddeck-gpu.cfg'
        displaced.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.switch(restore=True)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual(displaced.read_bytes(), b'foreign')


if __name__ == '__main__':
    unittest.main()
