# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('layer_preparation_fixtures', Path(__file__).with_name('machine_gpu_prepare_test.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
P = T.P


class LayerBoundary(unittest.TestCase):
    def test_internal_requires_supervisor_and_has_no_cli(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_saved_layer', None)), 'Owned machine saved-layer transition missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):
                P._machine_gpu_saved_layer(mock.Mock())
            for option in ('--machine-gpu-saved-layer', '--restore-machine-gpu-saved-layer'):
                with self.assertRaises(ValueError):
                    P.main([option])

    def test_default_active_guest_refuses_before_any_private_read(self):
        self.assertTrue(callable(getattr(P, '_machine_gpu_saved_layer', None)))
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._machine_gpu_saved_layer(mock.Mock(), supervised=lambda: None)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated saved-layer fixtures required')
class LayerTransition(unittest.TestCase):
    def setUp(self):
        self.fixture = T.PrepareTransaction()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.copier = self.fixture.copier
        self.current = self.fixture.saved
        self.original = self.current.read_bytes()
        self.original_inode = self.current.stat().st_ino
        self.on = self.current.parent / 'pending-droiddeck-layer-machine-on'
        self.off = self.current.parent / 'pending-droiddeck-layer-machine-off'
        self.state = self.fixture.parent / T.STATE
        self.manifest = (self.state / 'manifest.json').read_bytes()
        self.receipt = self.state / 'saved-layer.json'

    def change(self, *, restore=False, supervised=lambda: None):
        return P._machine_gpu_saved_layer(self.copier, self.fixture.parent, self.fixture.cfg,
            self.fixture.fixture.metadata_root, self.fixture.backup, restore=restore, supervised=supervised,
            offline=lambda stack, copier: self.fixture.fixture.admit(stack), profiles=self.fixture.profiles,
            verify_codecs=lambda _: None)

    def other_state_unchanged(self):
        self.assertEqual(self.fixture.cfg.read_bytes(), self.fixture.baseline)
        self.assertEqual((self.state / 'manifest.json').read_bytes(), self.manifest)
        self.assertFalse((self.fixture.misc / 'waydroid_settings').exists())

    def test_absence_apply_restore_preserves_original_inode_unknown_fields_and_no_document_export(self):
        result = self.change()
        candidate = self.current.read_bytes()
        self.assertEqual(self.fixture.layer.saved_value(candidate), 'true')
        parsed, _ = self.fixture.layer.parse(candidate)
        self.assertIn(('persist.fixture.unrelated', 'generated-private-marker'), [(p.name, p.value) for p in parsed.properties])
        self.assertIn(b'\x98\x06\x07', candidate)
        self.assertEqual(self.on.read_bytes(), self.original)
        self.assertEqual(self.on.stat().st_ino, self.original_inode)
        candidate_inode = self.current.stat().st_ino
        receipt = self.receipt.read_bytes()
        self.assertEqual(json.loads(receipt)['candidateInode'], candidate_inode)
        self.assertTrue(result['gpuLayerSelected'])
        self.assertTrue(result['savedPropertiesChanged'])
        self.assertFalse(result['configurationChanged'])
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'independentRecoveryArmed', 'servicesActivated', 'propertiesRegenerated', 'allocatorOverrideChanged',
                     'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])
        self.assertNotIn('generated-private-marker', json.dumps(result))
        self.assertNotIn(str(self.fixture.root), json.dumps(result))
        self.assertNotIn('generated-private-marker', receipt.decode())
        self.assertEqual(set(p.name for p in self.state.iterdir()), {'manifest.json', 'saved-layer.json'})
        self.change(restore=True)
        self.assertEqual(self.current.read_bytes(), self.original)
        self.assertEqual(self.current.stat().st_ino, self.original_inode)
        self.assertEqual(self.off.read_bytes(), candidate)
        self.assertEqual(self.off.stat().st_ino, candidate_inode)
        self.assertEqual(self.receipt.read_bytes(), receipt)
        self.assertFalse(self.on.exists())
        for file in (self.current, self.off):
            self.assertEqual((file.stat().st_uid, file.stat().st_gid, file.stat().st_mode & 0o777), (0, 0, 0o600))
        self.other_state_unchanged()

    def test_false_original_is_restored_exactly_not_as_absence(self):
        doc = self.fixture.layer.PropertyDocument()
        doc.properties.add(name=self.fixture.layer.KEY, value='false')
        doc.properties.add(name='persist.fixture.other', value='generated-only')
        original = doc.SerializeToString() + b'\x98\x06\x07'
        self.current.write_bytes(original)
        # Rebuild ONLY this generated fixture journal for a distinct false baseline.
        (self.state / 'manifest.json').unlink()
        self.state.rmdir()
        self.fixture.prepare()
        inode = self.current.stat().st_ino
        self.change()
        self.change(restore=True)
        self.assertEqual(self.current.read_bytes(), original)
        self.assertEqual(self.current.stat().st_ino, inode)

    def test_foreign_names_and_restore_destination_are_preserved(self):
        for file in (self.on, self.off, self.receipt):
            file.write_bytes(b'generated foreign')
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                self.change()
            self.assertEqual(file.read_bytes(), b'generated foreign')
            file.unlink()
        self.change()
        self.off.write_bytes(b'generated foreign retained history')
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.assertEqual(self.off.read_bytes(), b'generated foreign retained history')

    def test_same_bytes_foreign_candidate_or_baseline_inode_refuses_restore(self):
        self.change()
        for file in (self.current, self.on):
            displaced = self.current.parent / 'displaced-fixture'
            file.rename(displaced)
            file.write_bytes(displaced.read_bytes())
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                self.change(restore=True)
            file.unlink()
            displaced.rename(file)
        self.change(restore=True)

    def test_receipt_binding_duplicate_fields_or_wrong_hash_refuses(self):
        self.change()
        original = self.receipt.read_bytes()
        value = json.loads(original)
        for change in ({'version': True}, {'candidateSha256': 'f' * 64}, {'transactionManifestSha256': 'f' * 64},
                       {'candidateInode': value['candidateInode'] + 1}, {'credential': 'generated'}):
            self.receipt.write_text(json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n')
            with self.assertRaises(ValueError):
                self.change(restore=True)
        self.receipt.write_bytes(original.replace(b'"version": 1', b'"version": 1,"version": 1'))
        with self.assertRaises(ValueError):
            self.change(restore=True)

    def test_modified_unknown_property_or_software_config_refuses(self):
        self.change()
        for file in (self.current, self.on, self.fixture.cfg):
            original = file.read_bytes()
            file.write_bytes(original + b'generated changed bytes')
            with self.assertRaises(ValueError):
                self.change(restore=True)
            file.write_bytes(original)
        self.change(restore=True)

    def test_replay_and_restore_without_receipt_refuse(self):
        with self.assertRaises((ValueError, OSError)):
            self.change(restore=True)
        self.change()
        with self.assertRaises(ValueError):
            self.change()
        self.change(restore=True)
        with self.assertRaises(ValueError):
            self.change(restore=True)
        self.other_state_unchanged()

    def test_staged_candidate_recovery_after_preexchange_failure(self):
        with mock.patch.object(P, '_exchange_machine_gpu_names', side_effect=OSError('generated preexchange failure')), self.assertRaises(OSError):
            self.change()
        self.assertEqual(self.current.read_bytes(), self.original)
        self.assertEqual(self.fixture.layer.saved_value(self.on.read_bytes()), 'true')
        self.change(restore=True)
        self.assertEqual(self.current.stat().st_ino, self.original_inode)
        self.assertFalse(self.on.exists())
        self.assertTrue(self.off.exists())

    def test_timeout_after_apply_and_restore_exchange_retains_recoverable_pair(self):
        def active_timeout():
            if self.fixture.layer.saved_value(self.current.read_bytes()) == 'true':
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.change(supervised=active_timeout)
        def restored_timeout():
            if self.current.read_bytes() == self.original:
                raise subprocess.TimeoutExpired('generated supervisor', 5)
        with self.assertRaises(P.PromotionUncertain):
            self.change(restore=True, supervised=restored_timeout)
        self.change(restore=True)
        self.assertEqual(self.current.read_bytes(), self.original)
        self.assertEqual(self.current.stat().st_ino, self.original_inode)
        self.other_state_unchanged()

    def test_supervisor_or_metadata_refusal_prevents_exchange(self):
        def deny():
            raise ValueError('generated supervisor unavailable')
        with self.assertRaises(ValueError):
            self.change(supervised=deny)
        self.assertFalse(self.on.exists())
        write = self.copier.write_all
        def mutate(fd, data):
            write(fd, data)
            self.fixture.fixture.metadata.write_bytes(b'generated stale metadata')
        with mock.patch.object(self.copier, 'write_all', side_effect=mutate), self.assertRaises(ValueError):
            self.change()
        self.assertEqual(self.current.read_bytes(), self.original)

    def test_hardlinked_candidate_or_unsafe_property_directory_refuses_restore(self):
        self.change()
        linked = self.current.parent / 'linked-fixture'
        os.link(self.current, linked)
        with self.assertRaises(ValueError):
            self.change(restore=True)
        linked.unlink()
        self.current.parent.chmod(0o777)
        with self.assertRaises(ValueError):
            self.change(restore=True)

    def test_no_implicit_combination_with_config_or_allocator_state(self):
        for name in ('configuration.json', 'mesa-allocator-machine'):
            extra = self.state / name
            extra.write_bytes(b'generated foreign')
            with self.assertRaises(ValueError):
                self.change()
            self.assertFalse(self.on.exists())
            extra.unlink()


if __name__ == '__main__':
    unittest.main()
