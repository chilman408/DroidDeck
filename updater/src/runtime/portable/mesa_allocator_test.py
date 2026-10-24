# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class AllocatorCliTests(unittest.TestCase):
    def test_closed_cli_does_not_accept_path_or_enable_services(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_mesa_allocator') as install:
            for option, restore in [('--mesa-allocator', False), ('--restore-mesa-allocator', True)]:
                P.main([option])
                self.assertEqual(install.call_args.kwargs, {'restore': restore})
                with self.assertRaises(ValueError):
                    P.main([option, '/arbitrary-data'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class AllocatorTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-allocator-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.misc = self.root / 'misc'
        self.misc.mkdir()
        os.chown(self.misc, 1000, 9998)
        self.misc.chmod(0o1771)
        self.store = self.root / 'protected'
        self.store.mkdir(mode=0o700)
        self.identity = {'dataDevice': 1, 'dataInode': 2}
        self.file = self.misc / 'waydroid_settings'

    def apply(self, *, restore=False, guard=lambda: None, context=lambda: None, scope='proof'):
        return P._configure_mesa_allocator(self.copier, self.misc, self.store, self.identity,
            restore=restore, guard=guard, check_context=context, scope=scope)

    def test_install_receipt_then_restore_absence_retaining_owned_code(self):
        result = self.apply()
        self.assertEqual(self.file.read_bytes(), P.ALLOCATOR_CODE)
        self.assertTrue(result['allocatorPresent'])
        for key in ('servicesActivated', 'propertiesRegenerated', 'independentRecoveryArmed', 'readyForActivation'):
            self.assertFalse(result[key])
        receipt = self.store / 'mesa-allocator/receipt.json'
        original = receipt.read_bytes()
        metadata = json.loads(original)
        self.assertEqual(metadata['fileInode'], self.file.stat().st_ino)
        self.assertEqual((receipt.stat().st_mode & 0o777, receipt.stat().st_nlink), (0o600, 1))
        self.assertEqual(receipt.parent.stat().st_mode & 0o777, 0o700)
        result = self.apply(restore=True)
        self.assertFalse(result['allocatorPresent'])
        self.assertFalse(self.file.exists())
        retained = self.misc / P.ALLOCATOR_RESTORED
        self.assertEqual(retained.read_bytes(), P.ALLOCATOR_CODE)
        self.assertEqual(retained.stat().st_ino, metadata['fileInode'])
        self.assertEqual(receipt.read_bytes(), original)
        self.assertEqual((retained.stat().st_uid, retained.stat().st_gid, retained.stat().st_mode & 0o777, retained.stat().st_nlink), (0, 0, 0o600, 1))

    def test_existing_settings_or_namespace_is_not_changed(self):
        for name in ('waydroid_settings', P.ALLOCATOR_READY, P.ALLOCATOR_RESTORED):
            file = self.misc / name
            file.write_bytes(b'foreign')
            with self.assertRaises(ValueError):
                self.apply()
            self.assertEqual(file.read_bytes(), b'foreign')
            self.assertEqual(list(self.store.iterdir()), [])
            file.unlink()

    def test_install_restore_replay_refuse_without_removing_history(self):
        self.apply()
        with self.assertRaises(ValueError):
            self.apply()
        self.apply(restore=True)
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        with self.assertRaises(ValueError):
            self.apply()

    def test_recovery_refuses_foreign_inode_even_with_identical_bytes(self):
        self.apply()
        replacement = self.misc / 'replacement'
        replacement.write_bytes(P.ALLOCATOR_CODE)
        replacement.chmod(0o600)
        os.replace(replacement, self.file)
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.assertEqual(self.file.read_bytes(), P.ALLOCATOR_CODE)

    def test_corrupted_receipt_or_data_identity_refuses_recovery(self):
        self.apply()
        receipt = self.store / 'mesa-allocator/receipt.json'
        metadata = json.loads(receipt.read_bytes())
        metadata['dataInode'] += 1
        receipt.write_bytes(json.dumps(metadata).encode())
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.assertTrue(self.file.exists())

    def test_wrong_scope_or_extra_receipt_fields_refuse_recovery(self):
        self.apply()
        receipt = self.store / 'mesa-allocator/receipt.json'
        metadata = json.loads(receipt.read_bytes())
        for changed in ({**metadata, 'scope': 'activation'}, {**metadata, 'extra': 'foreign'}):
            receipt.write_bytes(json.dumps(changed).encode())
            with self.assertRaises(ValueError):
                self.apply(restore=True)
            self.assertTrue(self.file.exists())

    def test_wrong_mode_or_hardlink_refuses_recovery(self):
        self.apply()
        self.file.chmod(0o644)
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.file.chmod(0o600)
        os.link(self.file, self.misc / 'linked')
        with self.assertRaises(ValueError):
            self.apply(restore=True)

    def test_bad_misc_or_active_context_refuses_before_staging(self):
        self.misc.chmod(0o777)
        with self.assertRaises(ValueError):
            self.apply()
        self.misc.chmod(0o1771)
        def fail():
            raise ValueError('generated active/context refusal')
        for kwargs in ({'guard': fail}, {'context': fail}):
            with self.assertRaises(ValueError):
                self.apply(**kwargs)
        self.assertEqual(list(self.misc.iterdir()), [])
        self.assertEqual(list(self.store.iterdir()), [])

    def test_owned_prepublication_candidate_can_restore_without_activation(self):
        rename = P.rename_exclusive
        def fail(parent, source, target='images'):
            if target == 'waydroid_settings':
                raise OSError('generated prepublication failure')
            return rename(parent, source, target)
        with mock.patch.object(P, 'rename_exclusive', side_effect=fail), self.assertRaises(OSError):
            self.apply()
        self.assertFalse(self.file.exists())
        self.assertTrue((self.misc / P.ALLOCATOR_READY).exists())
        self.apply(restore=True)
        self.assertFalse((self.misc / P.ALLOCATOR_READY).exists())
        self.assertTrue((self.misc / P.ALLOCATOR_RESTORED).exists())

    def test_foreign_restore_destination_is_never_overwritten(self):
        self.apply()
        restored = self.misc / P.ALLOCATOR_RESTORED
        restored.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.apply(restore=True)
        self.assertEqual(restored.read_bytes(), b'foreign')
        self.assertTrue(self.file.exists())

    def test_postpublication_guard_failure_is_uncertain_with_owned_receipt(self):
        def guard():
            if self.file.exists():
                raise ValueError('generated active worker')
        with self.assertRaises(P.PromotionUncertain):
            self.apply(guard=guard)
        self.assertEqual(self.file.read_bytes(), P.ALLOCATOR_CODE)
        self.assertTrue((self.store / 'mesa-allocator/receipt.json').exists())

    def test_postpublication_sync_failure_retains_receipt_and_file_for_inspection(self):
        sync = os.fsync
        def fail(fd):
            if self.file.exists():
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.apply()
        self.assertEqual(self.file.read_bytes(), P.ALLOCATOR_CODE)
        self.assertTrue((self.store / 'mesa-allocator/receipt.json').exists())

    def test_separate_closed_activation_namespace_does_not_destroy_proof_history(self):
        self.apply()
        self.apply(restore=True)
        proof = (self.store / 'mesa-allocator/receipt.json').read_bytes()
        self.apply(scope='activation')
        self.apply(restore=True, scope='activation')
        self.assertEqual((self.store / 'mesa-allocator/receipt.json').read_bytes(), proof)
        self.assertTrue((self.misc / P.ALLOCATOR_RESTORED).exists())
        self.assertTrue((self.misc / 'pending-droiddeck-allocator-activation-restored').exists())
        with self.assertRaises(ValueError):
            self.apply(scope='../arbitrary')

    def test_postrename_root_race_preserves_foreign_bytes_and_reports_uncertainty(self):
        self.apply()
        rename = P.rename_exclusive
        def race(parent, source, target='images'):
            if target == P.ALLOCATOR_RESTORED:
                replacement = self.misc / 'foreign'
                replacement.write_bytes(b'foreign-preserved')
                replacement.chmod(0o600)
                os.replace(replacement, self.file)
            return rename(parent, source, target)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(P.PromotionUncertain):
            self.apply(restore=True)
        self.assertEqual((self.misc / P.ALLOCATOR_RESTORED).read_bytes(), b'foreign-preserved')

    def test_receipt_directory_swap_at_publication_is_uncertain_without_activation(self):
        rename = P.rename_exclusive
        def race(parent, source, target='images'):
            if target == 'mesa-allocator':
                staged = self.store / 'pending-mesa-allocator'
                staged.rename(self.store / 'retained-original')
                staged.mkdir(mode=0o700)
                (staged / 'receipt.json').write_bytes(b'foreign-preserved')
                (staged / 'receipt.json').chmod(0o600)
            return rename(parent, source, target)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(P.PromotionUncertain):
            self.apply()
        self.assertFalse(self.file.exists())
        self.assertEqual((self.store / 'mesa-allocator/receipt.json').read_bytes(), b'foreign-preserved')
        self.assertEqual((self.misc / P.ALLOCATOR_READY).read_bytes(), P.ALLOCATOR_CODE)


if __name__ == '__main__':
    unittest.main()
