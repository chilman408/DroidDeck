# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import subprocess
import unittest
from unittest import mock
import importlib.util


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


O = module('prepare_offline_fixtures', 'machine_offline_test.py')
M = module('prepare_profile_fixtures', 'machine_gpu_test.py')
P = O.P
STATE = 'machine-gpu-transaction'
PENDING = 'pending-' + STATE


class PrepareCli(unittest.TestCase):
    def test_closed_prepare_cli_does_not_offer_activation_or_path_overrides(self):
        self.assertTrue(callable(getattr(P, '_prepare_machine_gpu_transaction', None)), 'Machine compound preparation missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_prepare_machine_gpu_transaction') as prepare:
            P.main(['--prepare-machine-gpu-transaction'])
            self.assertEqual(prepare.call_args.args, (copier.return_value,))
            self.assertEqual(prepare.call_args.kwargs, {})
            for extra in ('--force', '--activate', '/other/data', 'a' * 64):
                with self.assertRaises(ValueError):
                    P.main(['--prepare-machine-gpu-transaction', extra])

    def test_default_busy_guard_precedes_parser_metadata_or_writes(self):
        self.assertTrue(callable(getattr(P, '_prepare_machine_gpu_transaction', None)), 'Machine compound preparation missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated busy')), \
                mock.patch.object(P, '_load_layer_parser', side_effect=AssertionError('must not load')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._prepare_machine_gpu_transaction(mock.Mock())


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated transaction fixtures required')
class PrepareTransaction(unittest.TestCase):
    def setUp(self):
        self.fixture = O.OfflineContext()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.copier, self.root = self.fixture.copier, self.fixture.root
        self.layer = P._load_layer_parser(self.copier)
        self.saved = self.fixture.property / 'persistent_properties'
        document = self.layer.PropertyDocument()
        document.properties.add(name='persist.fixture.unrelated', value='generated-private-marker')
        self.original = document.SerializeToString() + b'\x98\x06\x07'
        self.saved.write_bytes(self.original)
        self.saved.chmod(0o600)
        self.misc = self.fixture.data / 'misc'
        self.misc.mkdir(mode=0o1771)
        self.misc.chmod(0o1771)
        os.chown(self.misc, 1000, 9998)
        self.parent = self.root / 'state-parent'
        self.parent.mkdir(mode=0o700)
        self.backup = self.root / 'profiles'
        self.backup.mkdir(mode=0o700)
        for name, data in {'original.cfg': M.ORIGINAL, 'baseline.cfg': P.profile_candidate(M.ORIGINAL),
                           'gpu.cfg': P.profile_candidate(M.ORIGINAL, gpu=True)}.items():
            file = self.backup / name
            file.write_bytes(data)
            file.chmod(0o600)
        self.work = self.root / 'work'
        self.work.mkdir(mode=0o700)
        self.cfg = self.work / 'waydroid.cfg'
        self.baseline = P.profile_with_detected_protocols(P.profile_candidate(M.ORIGINAL))
        self.cfg.write_bytes(self.baseline)
        self.cfg.chmod(0o644)

    def profiles(self, copier, stack, metadata, backup):
        return P._hold_machine_gpu_policy(copier, stack, metadata, backup,
            validate=lambda encoded: {**self.fixture.validate(encoded), 'serviceManifestSha256': 'b' * 64}, inactive=lambda: None)

    def prepare(self, **kw):
        return P._prepare_machine_gpu_transaction(self.copier, self.parent, self.cfg, self.fixture.metadata_root, self.backup,
            offline=kw.get('offline', lambda stack, copier: self.fixture.admit(stack)), profiles=kw.get('profiles', self.profiles),
            verify_codecs=kw.get('verify_codecs', lambda _: None))

    def manifest(self):
        return json.loads((self.parent / STATE / 'manifest.json').read_bytes())

    def unchanged(self):
        self.assertEqual(self.saved.read_bytes(), self.original)
        self.assertEqual(self.cfg.read_bytes(), self.baseline)
        self.assertFalse((self.misc / 'waydroid_settings').exists())

    def test_durable_new_manifest_binds_machine_profiles_data_and_opaque_document_identity(self):
        before = (self.saved.stat().st_ino, self.cfg.stat().st_ino)
        result = self.prepare()
        manifest = self.manifest()
        self.assertEqual(manifest['stage'], 'prepared')
        self.assertEqual(manifest['configurationDigest'], O.GENERATION)
        self.assertEqual(manifest['metadataSha256'], O.sha(self.fixture.metadata.read_bytes()))
        self.assertEqual(manifest['guestReceiptSha256'], O.sha(self.fixture.marker.read_bytes()))
        self.assertEqual(manifest['softwareConfigurationSha256'], O.sha(self.baseline))
        self.assertEqual(manifest['savedDocumentSha256'], O.sha(self.original))
        self.assertEqual(manifest['beforeLayer'], 'absent')
        self.assertEqual(manifest['propertyInode'], before[0])
        self.assertEqual(manifest['configurationInode'], before[1])
        self.assertEqual(manifest['miscInode'], self.misc.stat().st_ino)
        self.assertEqual(stat.S_IMODE((self.parent / STATE).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.parent / STATE / 'manifest.json').stat().st_mode), 0o600)
        self.assertEqual(set(p.name for p in (self.parent / STATE).iterdir()), {'manifest.json'})
        self.assertNotIn('generated-private-marker', (self.parent / STATE / 'manifest.json').read_text())
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'servicesActivated', 'propertiesRegenerated', 'configurationChanged', 'savedPropertiesChanged',
                     'allocatorOverrideChanged', 'independentRecoveryArmed', 'androidOperationsExecuted', 'readyForActivation', 'installable'):
            self.assertFalse(result[flag])
        self.assertFalse((self.parent / PENDING).exists())
        self.unchanged()

    def test_false_layer_is_recorded_but_true_or_malformed_document_refuses(self):
        doc = self.layer.PropertyDocument()
        doc.properties.add(name=self.layer.KEY, value='false')
        self.saved.write_bytes(doc.SerializeToString())
        self.prepare()
        self.assertEqual(self.manifest()['beforeLayer'], 'false')
        (self.parent / STATE / 'manifest.json').unlink()
        (self.parent / STATE).rmdir()
        for raw in (self.layer.rewrite_optional(self.saved.read_bytes(), ('false',), 'true'), b'malformed'):
            self.saved.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse((self.parent / PENDING).exists())

    def test_existing_final_or_pending_namespace_is_never_overwritten(self):
        for name in (STATE, PENDING):
            directory = self.parent / name
            directory.mkdir(mode=0o700)
            (directory / 'foreign').write_bytes(b'generated sentinel')
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual((directory / 'foreign').read_bytes(), b'generated sentinel')
            (directory / 'foreign').unlink()
            directory.rmdir()
        self.unchanged()

    def test_replay_refuses_and_preserves_published_manifest(self):
        self.prepare()
        before = (self.parent / STATE / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.parent / STATE / 'manifest.json').read_bytes(), before)
        self.unchanged()

    def test_foreign_allocator_or_reserved_layer_or_configuration_names_refuse(self):
        for directory, name in ((self.misc, 'waydroid_settings'), (self.misc, 'pending-droiddeck-allocator-machine-ready'),
                (self.fixture.property, 'pending-droiddeck-layer-machine-on'), (self.work, 'pending-droiddeck-machine-gpu.cfg'),
                (self.parent, 'mesa-allocator-machine')):
            file = directory / name
            file.write_bytes(b'generated foreign')
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse((self.parent / STATE).exists())
            self.assertEqual(file.read_bytes(), b'generated foreign')
            file.unlink()
        self.unchanged()

    def test_profile_and_guest_metadata_must_be_same_generation(self):
        def mismatch(copier, stack, metadata, backup):
            policy, guard = self.profiles(copier, stack, metadata, backup)
            return {**policy, 'configurationDigest': 'd' * 64}, guard
        with self.assertRaises(ValueError):
            self.prepare(profiles=mismatch)
        self.assertFalse((self.parent / PENDING).exists())
        self.unchanged()

    def test_busy_or_codec_refusal_creates_no_state(self):
        def deny(*_):
            raise ValueError('generated refused')
        for kwargs in ({'offline': deny}, {'verify_codecs': deny}):
            with self.assertRaises(ValueError):
                self.prepare(**kwargs)
            self.assertFalse((self.parent / PENDING).exists())
        self.unchanged()

    def test_foreign_config_or_misc_owner_refuses_before_writing(self):
        self.cfg.write_bytes(b'generated foreign configuration')
        with self.assertRaises(ValueError):
            self.prepare()
        self.cfg.write_bytes(self.baseline)
        os.chown(self.misc, 1001, 9998)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.parent / PENDING).exists())

    def test_linked_property_document_or_unsafe_state_parent_refuses(self):
        linked = self.root / 'linked-document'
        os.link(self.saved, linked)
        with self.assertRaises(ValueError):
            self.prepare()
        linked.unlink()
        self.parent.chmod(0o777)
        with self.assertRaises(ValueError):
            self.prepare()
        self.unchanged()

    def test_property_mutation_during_write_retains_partial_metadata_without_publishing(self):
        original_write = self.copier.write_all
        def mutate(fd, data):
            original_write(fd, data)
            self.saved.write_bytes(b'generated changed private fixture')
        with mock.patch.object(self.copier, 'write_all', side_effect=mutate), self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.parent / PENDING).exists())
        self.assertFalse((self.parent / STATE).exists())
        self.assertEqual(self.cfg.read_bytes(), self.baseline)

    def test_raced_final_directory_is_never_replaced(self):
        rename = P.rename_exclusive
        def race(parent, pending, final):
            (self.parent / STATE).mkdir(mode=0o700)
            (self.parent / STATE / 'foreign').write_bytes(b'generated sentinel')
            return rename(parent, pending, final)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual((self.parent / STATE / 'foreign').read_bytes(), b'generated sentinel')
        self.assertTrue((self.parent / PENDING / 'manifest.json').exists())
        self.unchanged()

    def test_same_bytes_configuration_replacement_during_write_refuses(self):
        write = self.copier.write_all
        def replace(fd, data):
            write(fd, data)
            replacement = self.work / 'replacement'
            replacement.write_bytes(self.baseline)
            replacement.chmod(0o644)
            os.replace(replacement, self.cfg)
        with mock.patch.object(self.copier, 'write_all', side_effect=replace), self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.parent / PENDING).exists())
        self.assertFalse((self.parent / STATE).exists())
        self.unchanged()

    def test_manifest_corruption_during_write_never_publishes(self):
        write = self.copier.write_all
        def corrupt(fd, data):
            write(fd, data + b'generated foreign suffix')
        with mock.patch.object(self.copier, 'write_all', side_effect=corrupt), self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.parent / PENDING).exists())
        self.assertFalse((self.parent / STATE).exists())
        self.unchanged()

    def test_postpublication_guard_timeout_reports_uncertain_and_retains_checkpoint(self):
        def context(stack, copier):
            value = self.fixture.admit(stack)
            check = value['check']
            def timed_out():
                check()
                if (self.parent / STATE).exists():
                    raise subprocess.TimeoutExpired('generated stopped guard', 5)
            return {**value, 'check': timed_out}
        with self.assertRaises(P.PromotionUncertain):
            self.prepare(offline=context)
        self.assertEqual(self.manifest()['stage'], 'prepared')
        self.unchanged()


if __name__ == '__main__':
    unittest.main()
