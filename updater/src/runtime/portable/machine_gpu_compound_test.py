# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import itertools
import json
import os
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('compound_prepare_fixtures', Path(__file__).with_name('machine_gpu_prepare_test.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
P = T.P


class CompoundBoundary(unittest.TestCase):
    def test_stopped_before_reads_and_no_compound_cli_or_mutator(self):
        self.assertTrue(callable(getattr(P, '_hold_machine_gpu_compound', None)), 'Compound ownership admission missing')
        with ExitStack() as stack, mock.patch.object(P.sys, 'platform', 'linux'), \
                mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, '_assert_machine_guest_stopped', side_effect=ValueError('generated active')), \
                mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            P._hold_machine_gpu_compound(mock.Mock(), stack)
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
            for option in ('--machine-gpu-compound', '--recover-machine-gpu-compound'):
                with self.assertRaises(ValueError):
                    P.main([option])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated compound fixtures required')
class CompoundOwnership(unittest.TestCase):
    def setUp(self):
        self.fixture = T.PrepareTransaction()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.copier = self.fixture.copier
        self.state = self.fixture.parent / T.STATE
        self.marker = self.state / 'manifest.json'
        self.original_marker = self.marker.read_bytes()
        self.manifest_sha = T.O.sha(self.original_marker)
        self.generated = []
        self.exchanges = []
        self.layout = {
            'configuration': (self.fixture.cfg, 'pending-droiddeck-machine-gpu.cfg', 'pending-droiddeck-machine-gpu-restore.cfg',
                              P.profile_with_detected_protocols(P.profile_candidate(T.M.ORIGINAL, gpu=True)), 0o644, 'configuration.json'),
            'savedLayer': (self.fixture.saved, 'pending-droiddeck-layer-machine-on', 'pending-droiddeck-layer-machine-off',
                           self.fixture.layer.rewrite_optional(self.fixture.original, ('absent',), 'true'), 0o600, 'saved-layer.json')}

    def kwargs(self):
        return {'offline': lambda stack, copier: self.fixture.fixture.admit(stack),
                'profiles': self.fixture.profiles, 'verify_codecs': lambda _: None}

    def hold(self, stack):
        return P._hold_machine_gpu_compound(self.copier, stack, self.fixture.parent, self.fixture.cfg,
            self.fixture.fixture.metadata_root, self.fixture.backup, **self.kwargs())

    def reset_generated(self):
        for current, displaced in reversed(self.exchanges):
            current.unlink()
            displaced.rename(current)
        self.exchanges.clear()
        for file in reversed(self.generated):
            file.unlink() if file.is_file() else file.rmdir()
        self.generated.clear()

    def select(self, role, status):
        # Simulate individually verified owned outcomes in generated root-private data only.
        if status == 'baseline':
            return
        if role == 'allocator':
            directory = self.state / 'mesa-allocator-machine'
            directory.mkdir(mode=0o700)
            self.generated.append(directory)
            name = {'selected': 'waydroid_settings', 'staged': 'pending-droiddeck-allocator-machine-ready',
                    'restored': 'pending-droiddeck-allocator-machine-restored'}[status]
            file = self.fixture.misc / name
            file.write_bytes(P.ALLOCATOR_CODE)
            file.chmod(0o600)
            self.generated.append(file)
            info, misc = file.stat(), self.fixture.misc.stat()
            value = {'kind': 'protected-mesa-allocator', 'version': 1, 'beforeAbsent': True, 'scope': 'machine',
                     'candidateSha256': T.O.sha(P.ALLOCATOR_CODE), 'dataDevice': self.fixture.fixture.data.stat().st_dev,
                     'dataInode': self.fixture.fixture.data.stat().st_ino, 'miscDevice': misc.st_dev, 'miscInode': misc.st_ino,
                     'fileDevice': info.st_dev, 'fileInode': info.st_ino}
            receipt = directory / 'receipt.json'
            encoded = json.dumps(value, indent=2) + '\n'
        else:
            current, on, off, data, mode, receipt_name = self.layout[role]
            file = current.parent / (off if status == 'restored' else on)
            file.write_bytes(data)
            file.chmod(mode)
            info = file.stat()
            if status == 'selected':
                displaced = current.parent / on
                candidate = current.parent / 'fixture-candidate'
                file.rename(candidate)
                current.rename(displaced)
                candidate.rename(current)
                self.exchanges.append((current, displaced))
            else:
                self.generated.append(file)
            value = {'kind': 'protected-machine-gpu-' + ('saved-layer' if role == 'savedLayer' else 'configuration'),
                     'version': 1, 'transactionManifestSha256': self.manifest_sha, 'candidateDevice': info.st_dev,
                     'candidateInode': info.st_ino, 'candidateSha256': T.O.sha(data)}
            receipt = self.state / receipt_name
            encoded = json.dumps(value, sort_keys=True, indent=2) + '\n'
        receipt.write_text(encoded)
        receipt.chmod(0o600)
        self.generated.append(receipt)

    def test_all_64_owned_combinations_admit_without_writes_or_document_export(self):
        for combination in itertools.product(('baseline', 'staged', 'selected', 'restored'), repeat=3):
            states = dict(zip(('configuration', 'savedLayer', 'allocator'), combination))
            with self.subTest(states=states):
                for role, status in states.items():
                    self.select(role, status)
                snapshot = {str(p): (p.stat().st_ino, p.read_bytes()) for parent in
                            (self.state, self.fixture.work, self.fixture.saved.parent, self.fixture.misc)
                            for p in parent.rglob('*') if p.is_file()}
                with ExitStack() as stack:
                    admitted = self.hold(stack)
                    admitted['check']()
                    self.assertEqual(admitted['states'], states)
                after = {str(p): (p.stat().st_ino, p.read_bytes()) for parent in
                         (self.state, self.fixture.work, self.fixture.saved.parent, self.fixture.misc)
                         for p in parent.rglob('*') if p.is_file()}
                self.assertEqual(snapshot, after)
            self.reset_generated()

    def test_receipt_stale_hash_duplicate_and_extra_field_refuse(self):
        self.select('configuration', 'selected')
        receipt = self.state / 'configuration.json'
        original = receipt.read_bytes()
        value = json.loads(original)
        for change in ({'version': True}, {'candidateSha256': 'f' * 64}, {'transactionManifestSha256': 'f' * 64}, {'unknown': False}):
            receipt.write_text(json.dumps({**value, **change}, sort_keys=True, indent=2) + '\n')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
        receipt.write_bytes(original.replace(b'"version": 1', b'"version": 1,"version": 1'))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_real_component_fixture_outputs_admit_selected_and_restored(self):
        for role, writer in (('configuration', P._machine_gpu_configuration), ('savedLayer', P._machine_gpu_saved_layer),
                             ('allocator', P._machine_gpu_allocator)):
            fixture = T.PrepareTransaction()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.prepare()
            kwargs = {'offline': lambda stack, copier: fixture.fixture.admit(stack),
                      'profiles': fixture.profiles, 'verify_codecs': lambda _: None}
            for restore in (False, True):
                writer(fixture.copier, fixture.parent, fixture.cfg, fixture.fixture.metadata_root, fixture.backup,
                       restore=restore, supervised=lambda: None, **kwargs)
                with ExitStack() as stack:
                    ctx = P._hold_machine_gpu_compound(fixture.copier, stack, fixture.parent, fixture.cfg,
                        fixture.fixture.metadata_root, fixture.backup, **kwargs)
                    ctx['check']()
                    self.assertEqual(ctx['states'][role], 'restored' if restore else 'selected')
                    self.assertTrue(all(status == 'baseline' for name, status in ctx['states'].items() if name != role))

    def test_foreign_same_bytes_current_or_original_inode_refuses(self):
        self.select('configuration', 'selected')
        self.select('savedLayer', 'selected')
        for file in (self.fixture.cfg, self.fixture.saved, self.fixture.saved.parent / self.layout['savedLayer'][1]):
            displaced = file.parent / 'kept-generated'
            file.rename(displaced)
            file.write_bytes(displaced.read_bytes())
            file.chmod(displaced.stat().st_mode & 0o777)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            file.unlink()
            displaced.rename(file)

    def test_foreign_allocator_inode_or_ambiguous_candidate_names_refuse(self):
        self.select('allocator', 'selected')
        file = self.fixture.misc / 'waydroid_settings'
        replacement = self.fixture.misc / 'replacement'
        replacement.write_bytes(P.ALLOCATOR_CODE)
        replacement.chmod(0o600)
        os.replace(replacement, file)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        value = json.loads((self.state / 'mesa-allocator-machine/receipt.json').read_bytes())
        value.update(fileDevice=file.stat().st_dev, fileInode=file.stat().st_ino)
        (self.state / 'mesa-allocator-machine/receipt.json').write_text(json.dumps(value, indent=2) + '\n')
        ready = self.fixture.misc / 'pending-droiddeck-allocator-machine-ready'
        ready.write_bytes(P.ALLOCATOR_CODE)
        ready.chmod(0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_partial_and_foreign_state_inventory_refuses(self):
        for directory, name in ((self.state, 'pending-mesa-allocator-machine'), (self.state, 'foreign.pem'),
                                (self.fixture.parent, T.PENDING)):
            file = directory / name
            file.write_bytes(b'generated foreign')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(file.read_bytes(), b'generated foreign')
            file.unlink()

    def test_unreceipted_or_missing_displaced_document_refuses(self):
        self.select('savedLayer', 'selected')
        receipt = self.state / 'saved-layer.json'
        original = receipt.read_bytes()
        receipt.unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        receipt.write_bytes(original)
        receipt.chmod(0o600)
        (self.fixture.saved.parent / self.layout['savedLayer'][1]).unlink()
        with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
            self.hold(stack)

    def test_held_receipt_payload_or_metadata_mutation_refuses_recheck(self):
        self.select('configuration', 'selected')
        self.select('savedLayer', 'selected')
        self.select('allocator', 'selected')
        for file in (self.state / 'configuration.json', self.fixture.cfg, self.fixture.saved,
                     self.fixture.misc / 'waydroid_settings', self.fixture.fixture.metadata):
            original = file.read_bytes()
            with ExitStack() as stack:
                admitted = self.hold(stack)
                file.write_bytes(original + b'generated change')
                with self.assertRaises(ValueError):
                    admitted['check']()
            file.write_bytes(original)

    def test_same_bytes_receipt_inode_replacement_refuses_recheck(self):
        self.select('savedLayer', 'staged')
        with ExitStack() as stack:
            admitted = self.hold(stack)
            receipt = self.state / 'saved-layer.json'
            replacement = self.state / 'replacement'
            replacement.write_bytes(receipt.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, receipt)
            with self.assertRaises(ValueError):
                admitted['check']()

    def test_directory_lock_excludes_existing_component_admission(self):
        with ExitStack() as first:
            self.hold(first)
            with ExitStack() as second, self.assertRaises(BlockingIOError):
                P._hold_machine_gpu_transaction(self.copier, second, self.fixture.parent, self.fixture.cfg,
                    self.fixture.fixture.metadata_root, self.fixture.backup, **self.kwargs())
        with ExitStack() as stack:
            self.hold(stack)

    def test_wrong_allocator_scope_links_and_writable_state_refuse(self):
        self.select('allocator', 'staged')
        receipt = self.state / 'mesa-allocator-machine/receipt.json'
        original = receipt.read_bytes()
        receipt.write_bytes(original.replace(b'"machine"', b'"activation"'))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        receipt.write_bytes(original)
        linked = self.fixture.root / 'linked'
        os.link(receipt, linked)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        linked.unlink()
        self.state.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_sanitized_policy_has_no_paths_documents_or_activation_claim(self):
        for role in self.layout:
            self.select(role, 'selected')
        self.select('allocator', 'restored')
        with ExitStack() as stack:
            admitted = self.hold(stack)
            with mock.patch.object(P, '_hold_machine_gpu_compound', return_value=admitted):
                result = P._machine_gpu_compound_policy(self.copier)
        self.assertEqual(result['states'], {'configuration': 'selected', 'savedLayer': 'selected', 'allocator': 'restored'})
        self.assertNotIn(str(self.fixture.root), json.dumps(result))
        self.assertNotIn('generated-private-marker', json.dumps(result))
        self.assertNotIn('savedDocumentSha256', result)
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'codecsVerified',
                     'configurationChanged', 'savedPropertiesChanged', 'allocatorOverrideChanged', 'servicesActivated',
                     'propertiesRegenerated', 'androidOperationsExecuted', 'independentRecoveryArmed', 'readyForActivation', 'installable'):
            self.assertFalse(result[flag])


if __name__ == '__main__':
    unittest.main()
