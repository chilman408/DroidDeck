# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('configuration_preparation_fixtures', Path(__file__).with_name('machine_gpu_prepare_test.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
P = T.P


class ConfigurationBoundary(unittest.TestCase):
    def test_internal_requires_supervisor_and_has_no_cli(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_configuration', None)), 'Owned machine configuration transition missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):
                P._machine_gpu_configuration(mock.Mock())
            for option in ('--machine-gpu-configuration', '--restore-machine-gpu-configuration'):
                with self.assertRaises(ValueError):
                    P.main([option])

    def test_default_stopped_guard_precedes_any_private_read(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_configuration', None)))
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._machine_gpu_configuration(mock.Mock(), supervised=lambda: None)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated configuration fixtures required')
class ConfigurationTransition(unittest.TestCase):
    def setUp(self):
        self.fixture = T.PrepareTransaction()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.copier = self.fixture.copier
        self.cfg = self.fixture.cfg
        self.on = self.fixture.work / 'pending-droiddeck-machine-gpu.cfg'
        self.off = self.fixture.work / 'pending-droiddeck-machine-gpu-restore.cfg'
        self.state = self.fixture.parent / T.STATE
        self.marker = self.state / 'manifest.json'
        self.receipt = self.state / 'configuration.json'
        self.manifest = self.marker.read_bytes()
        self.original_inode = self.cfg.stat().st_ino
        self.gpu = P.profile_with_detected_protocols(P.profile_candidate(T.M.ORIGINAL, gpu=True))

    def change(self, *, restore=False, supervised=lambda: None):
        return P._machine_gpu_configuration(self.copier, self.fixture.parent, self.cfg,
            self.fixture.fixture.metadata_root, self.fixture.backup, restore=restore, supervised=supervised,
            offline=lambda stack, copier: self.fixture.fixture.admit(stack), profiles=self.fixture.profiles,
            verify_codecs=lambda _: None)

    def others_unchanged(self):
        self.assertEqual(self.marker.read_bytes(), self.manifest)
        self.assertEqual(self.fixture.saved.read_bytes(), self.fixture.original)
        self.assertFalse((self.fixture.misc / 'waydroid_settings').exists())

    def test_apply_restore_retains_exact_original_inode_and_gpu_receipt(self):
        result = self.change()
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.assertEqual(self.on.read_bytes(), self.fixture.baseline)
        self.assertEqual(self.on.stat().st_ino, self.original_inode)
        gpu_inode = self.cfg.stat().st_ino
        receipt = self.receipt.read_bytes()
        self.assertEqual(json.loads(receipt)['candidateInode'], gpu_inode)
        self.assertTrue(result['gpuConfigurationSelected'])
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'independentRecoveryArmed', 'servicesActivated', 'propertiesRegenerated', 'savedPropertiesChanged',
                     'allocatorOverrideChanged', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])
        self.assertNotIn(str(self.fixture.root), json.dumps(result))
        result = self.change(restore=True)
        self.assertFalse(result['gpuConfigurationSelected'])
        self.assertEqual(self.cfg.read_bytes(), self.fixture.baseline)
        self.assertEqual(self.cfg.stat().st_ino, self.original_inode)
        self.assertEqual(self.off.read_bytes(), self.gpu)
        self.assertEqual(self.off.stat().st_ino, gpu_inode)
        self.assertFalse(self.on.exists())
        self.assertEqual(self.receipt.read_bytes(), receipt)
        self.others_unchanged()

    def test_foreign_current_config_or_existing_names_refuse(self):
        for file in (self.on, self.off, self.receipt):
            file.write_bytes(b'generated foreign sentinel')
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                self.change()
            self.assertEqual(file.read_bytes(), b'generated foreign sentinel')
            file.unlink()
        self.cfg.write_bytes(b'generated foreign config')
        with self.assertRaises(ValueError):
            self.change()
        self.assertFalse(self.on.exists())

    def test_replay_apply_and_restore_refuse_without_overwrite(self):
        self.change()
        with self.assertRaises(ValueError):
            self.change()
        self.change(restore=True)
        retained = self.off.read_bytes()
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.assertEqual(self.off.read_bytes(), retained)
        self.others_unchanged()

    def test_same_bytes_foreign_gpu_or_displaced_baseline_inode_is_not_owned(self):
        self.change()
        for file in (self.cfg, self.on):
            original = file.read_bytes()
            replacement = self.fixture.work / 'replacement'
            replacement.write_bytes(original)
            replacement.chmod(0o644)
            displaced = self.fixture.work / ('retained-' + file.name)
            file.rename(displaced)
            replacement.rename(file)
            with self.assertRaises(ValueError):
                self.change(restore=True)
            self.assertEqual(file.read_bytes(), original)
            file.unlink()
            displaced.rename(file)
        self.change(restore=True)

    def test_receipt_duplicate_extra_wrong_binding_or_candidate_hash_refuses(self):
        self.change()
        original = self.receipt.read_bytes()
        value = json.loads(original)
        for change in ({'version': True}, {'transactionManifestSha256': 'f' * 64}, {'candidateSha256': 'f' * 64},
                       {'candidateInode': value['candidateInode'] + 1}, {'credential': 'generated'}):
            self.receipt.write_text(json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n')
            with self.assertRaises(ValueError):
                self.change(restore=True)
            self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.receipt.write_bytes(original.replace(b'"version": 1', b'"version": 1,"version": 1'))
        with self.assertRaises(ValueError):
            self.change(restore=True)

    def test_layer_or_manifest_change_prevents_restore_without_overwriting(self):
        self.change()
        for file, changed in ((self.fixture.saved, b'generated changed opaque document'), (self.marker, self.manifest + b' ')):
            original = file.read_bytes()
            file.write_bytes(changed)
            with self.assertRaises(ValueError):
                self.change(restore=True)
            self.assertEqual(self.cfg.read_bytes(), self.gpu)
            file.write_bytes(original)
        self.change(restore=True)

    def test_supervisor_refusal_and_default_restore_without_receipt_do_not_write(self):
        def deny():
            raise ValueError('generated supervisor unavailable')
        with self.assertRaises(ValueError):
            self.change(supervised=deny)
        with self.assertRaises((ValueError, OSError)):
            self.change(restore=True)
        self.assertFalse(self.on.exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.cfg.stat().st_ino, self.original_inode)

    def test_staged_candidate_can_restore_after_failure_before_exchange(self):
        with mock.patch.object(P, '_exchange_machine_gpu_names', side_effect=OSError('generated preexchange failure')), self.assertRaises(OSError):
            self.change()
        self.assertEqual(self.cfg.read_bytes(), self.fixture.baseline)
        self.assertEqual(self.on.read_bytes(), self.gpu)
        self.assertTrue(self.receipt.exists())
        self.change(restore=True)
        self.assertEqual(self.cfg.stat().st_ino, self.original_inode)
        self.assertEqual(self.off.read_bytes(), self.gpu)
        self.others_unchanged()

    def test_timeout_after_apply_is_uncertain_with_owned_restore_available(self):
        def timeout():
            if self.cfg.read_bytes() == self.gpu:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.change(supervised=timeout)
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        self.change(restore=True)
        self.others_unchanged()

    def test_timeout_after_restore_exchange_can_finish_retaining_candidate(self):
        self.change()
        def timeout():
            if self.cfg.read_bytes() == self.fixture.baseline:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.change(restore=True, supervised=timeout)
        self.assertEqual(self.cfg.stat().st_ino, self.original_inode)
        self.change(restore=True)
        self.assertFalse(self.on.exists())
        self.assertEqual(self.off.read_bytes(), self.gpu)
        self.others_unchanged()

    def test_foreign_restore_destination_is_preserved(self):
        self.change()
        self.off.write_bytes(b'generated foreign history')
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.assertEqual(self.off.read_bytes(), b'generated foreign history')
        self.assertEqual(self.cfg.read_bytes(), self.gpu)

    def test_receipt_inode_replacement_at_exchange_reports_uncertainty_and_preserves_foreign_file(self):
        exchange = P._exchange_machine_gpu_names
        def replace(directory, current, pending):
            exchange(directory, current, pending)
            replacement = self.state / 'replacement'
            replacement.write_bytes(b'generated foreign receipt')
            replacement.chmod(0o600)
            os.replace(replacement, self.receipt)
        with mock.patch.object(P, '_exchange_machine_gpu_names', side_effect=replace), self.assertRaises(P.PromotionUncertain):
            self.change()
        self.assertEqual(self.receipt.read_bytes(), b'generated foreign receipt')
        self.assertEqual(self.cfg.read_bytes(), self.gpu)
        with self.assertRaises(ValueError):
            self.change(restore=True)

    def test_hardlinked_candidate_or_receipt_prevents_restore(self):
        self.change()
        for file in (self.cfg, self.receipt):
            linked = self.fixture.root / 'linked'
            os.link(file, linked)
            with self.assertRaises(ValueError):
                self.change(restore=True)
            linked.unlink()
        self.change(restore=True)
        self.others_unchanged()

    def test_metadata_mutation_during_candidate_write_prevents_exchange(self):
        write = self.copier.write_all
        def mutate(fd, data):
            write(fd, data)
            self.fixture.fixture.metadata.write_bytes(b'generated metadata changed')
        with mock.patch.object(self.copier, 'write_all', side_effect=mutate), self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.cfg.read_bytes(), self.fixture.baseline)
        self.assertEqual(self.cfg.stat().st_ino, self.original_inode)

    def test_allocator_transaction_or_unknown_state_inventory_is_not_combined_implicitly(self):
        extra = self.state / 'mesa-allocator-machine'
        extra.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.change()
        self.assertFalse(self.on.exists())
        self.assertTrue(extra.exists())


if __name__ == '__main__':
    unittest.main()
