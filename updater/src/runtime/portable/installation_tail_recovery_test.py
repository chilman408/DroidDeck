# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('tail_tested', Path(__file__).with_name('installation-tail-recovery.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def proof():
    return {'kind': 'installation-tail-audit', 'installationCompletionVerified': False,
            'originalInstallationOwnershipVerified': True, 'completedPhasesVerified': 31,
            'recoveredTailPhasesVerified': 4, 'base': {'version': '0.1.0-rc.2', 'sourceSha256': 'a' * 64},
            'tailAuthority': {'originalGpu': 'a' * 12, 'currentGpu': 'b' * 12, 'generated': True}}


class Boundary(unittest.TestCase):
    def setUp(self):
        self.base = D.load('update-base')
        self.phases = self.base.phase_names(Path(__file__).with_name('install-machine.py').read_bytes())
        self.config = {'generated': True}
        self.intent = {'schema': 1, 'configuration': self.config, 'machineSha256': 'a' * 64,
                       'packageManifestSha256': self.base.PACKAGE_PIN}
        self.markers = {name + suffix: D.H.encode({'phase': name}) for name in self.phases if name not in D.TAIL
                        for suffix in ('.started.json', '.complete.json')}
        self.markers[D.TAIL[0] + '.started.json'] = D.H.encode({'phase': D.TAIL[0]})

    def check(self, **kwargs):
        args = dict(intent=self.intent, config=self.config, machine_pin='a' * 64, package_pin=self.base.PACKAGE_PIN,
                    phases=self.phases, markers=self.markers)
        D.interrupted(**{**args, **kwargs})

    def test_exact_failed_boundary_requires31_completed_and_only_defaults_started(self):
        self.check()
        self.assertEqual(len(self.markers), 63)

    def test_missing_or_extra_original_receipts_refuse(self):
        for markers in ({}, {k: v for k, v in self.markers.items() if k != '01-host.complete.json'},
                        {**self.markers, '31-required-defaults.complete.json': D.H.encode({'phase': D.TAIL[0]})},
                        {**self.markers, '32-wake-initial-display.started.json': b'{}\n'}):
            with self.assertRaises(ValueError):
                self.check(markers=markers)

    def test_changed_machine_package_configuration_or_schema_refuse(self):
        for key, value in (('machineSha256', 'b' * 64), ('packageManifestSha256', 'b' * 64),
                           ('configuration', {}), ('schema', True), ('extra', True)):
            with self.assertRaises(ValueError):
                self.check(intent={**self.intent, key: value})

    def test_changed_or_noncanonical_phase_bytes_and_changed_phase_inventory_refuse(self):
        with self.assertRaises(ValueError):
            self.check(markers={**self.markers, '01-host.complete.json': b'{ "phase":"01-host"}\n'})
        with self.assertRaises(ValueError):
            self.check(phases=self.phases[:-1])

    def succession(self):
        old, new = 'a' * 12, 'b' * 12
        boot = '11111111-1111-1111-1111-111111111111'
        admissions = {name: {'version': 1, 'kind': 'original-owned-fresh-gpu', 'transaction': name,
                            'configuration': self.config, 'metadataSha256': 'c' * 64,
                            'guestReceiptSha256': 'd' * 64, 'dataDevice': 1, 'dataInode': 2} for name in (old, new)}
        statuses = {old: {'boot': boot, 'since': 1, 'stage': 'restored'},
                    new: {'boot': boot, 'since': 2, 'stage': 'ready'}}
        manifest = {'version': 1, 'cpu': 'e' * 64, 'gpu': 'f' * 64, 'beforeLayer': 'absent',
                    'dataDevice': 1, 'dataInode': 2}
        return dict(admissions=admissions, statuses=statuses, manifests={name: dict(manifest) for name in (old, new)},
                    original=old, current=new, config=self.config, metadata_pin='c' * 64,
                    cpu='e' * 64, gpu='f' * 64, boot=boot)

    def test_same_installation_terminal_to_current_ready_succession(self):
        args = self.succession()
        self.assertEqual(D.gpu_pair(**args)['dataInode'], 2)

    def test_foreign_data_guest_receipt_metadata_or_token_refused(self):
        for key, value in (('dataInode', 3), ('guestReceiptSha256', 'e' * 64), ('metadataSha256', 'e' * 64),
                           ('configuration', {}), ('transaction', 'c' * 12), ('version', True)):
            args = self.succession()
            args['admissions'][args['current']][key] = value
            with self.assertRaises(ValueError):
                D.gpu_pair(**args)

    def test_pending_predecessor_stale_or_software_current_gpu_refused(self):
        for name, field, value in (('original', 'stage', 'ready'), ('original', 'stage', 'recovering'),
                                    ('current', 'stage', 'restored'), ('current', 'boot', '2' * 36),
                                    ('current', 'since', float('nan'))):
            args = self.succession()
            args['statuses'][args[name]][field] = value
            with self.assertRaises(ValueError):
                D.gpu_pair(**args)

    def test_incomplete_or_unbound_manifest_refused(self):
        args = self.succession()
        args['manifests'][args['current']]['beforeLayer'] = 'true'
        with self.assertRaises(ValueError):
            D.gpu_pair(**args)
        args = self.succession()
        args['admissions'].pop(args['original'])
        with self.assertRaises(ValueError):
            D.gpu_pair(**args)

    def test_certificate_does_not_forge_old_completion_or_update_admission(self):
        result = D.certificate(proof())
        self.assertFalse(result['originalCompletionWritten'])
        self.assertFalse(result['updateAdmissionCreated'])
        self.assertEqual(result['originalCompletedPhases'], 31)
        self.assertEqual(result['recoveredTailPhases'], list(D.TAIL))
        self.assertNotEqual(result['authoritySha256'], D.certificate({**proof(), 'tailAuthority':
                            {**proof()['tailAuthority'], 'generated': False}})['authoritySha256'])

    def test_unverified_or_false_original_phase_count_cannot_be_sealed(self):
        for key, value in (('kind', 'installed-update-base-inspection'), ('installationCompletionVerified', True),
                           ('originalInstallationOwnershipVerified', False), ('completedPhasesVerified', 35),
                           ('recoveredTailPhasesVerified', 3)):
            with self.assertRaises(ValueError):
                D.certificate({**proof(), key: value})

    def test_token_traversal_nonexact_and_adoption_not_supported(self):
        for token in ('../other', '', 'A' * 12, 1, 'a' * 13):
            with self.assertRaises(ValueError):
                D.token(token)

    def test_sdk36_global_awake_with_numeric_group_state_and_ambiguous_or_sleep_refusal(self):
        self.assertTrue(D.awake('  mWakefulness=Awake\n  mWakefulnessChanging=false\n    mWakefulness=1\n'))
        for output in ('mWakefulness=1\n', 'mWakefulness=Asleep\nmWakefulness=1\n',
                       'mWakefulness=Awake\nmWakefulness=Awake\n', 'mWakefulness=Unknown\n',
                       'mWakefulness=Unknown\nmWakefulness=Awake\n'):
            self.assertFalse(D.awake(output))

    def test_immutable_receipt_generations_bound_but_current_boot_status_can_advance(self):
        records = SimpleNamespace(files={'/root/original.json': (1, 'a' * 64, 0o600, (1, 2, 3)),
            '/var/lib/droiddeck/gpu-transactions/' + 'b' * 12 + '/status.json': (1, 'c' * 64, 0o600, (4, 5))})
        self.assertEqual(D.receipt_generations(records, 'b' * 12), {'/root/original.json': [1, 2, 3]})

    def test_normal_inspection_still_requires_original_completion(self):
        with self.assertRaises(ValueError):
            self.base.completed(self.intent, {}, self.config, 'a' * 64)
        with mock.patch.object(self.base, 'inspect', return_value={'strict': True}) as inspect, \
                mock.patch.object(self.base, 'load') as load:
            self.base.main(['inspect', '/root/pinned'])
            inspect.assert_called_once_with('/root/pinned')
            load.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux isolated recovery journal fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-tail-fixture-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'recovery'
        patch = mock.patch.object(D, 'ROOT', self.target)
        patch.start()
        self.addCleanup(patch.stop)

    def test_new_certificate_seals_without_any_original_installation_writes(self):
        prove = mock.Mock(side_effect=lambda: proof())
        expected = D.seal(prove)
        self.assertEqual(prove.call_count, 2)
        self.assertEqual(set(p.name for p in self.target.iterdir()), {'intent.json', 'complete.json'})
        for path in self.target.iterdir():
            self.assertEqual(path.read_bytes(), D.H.encode(expected))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)

    def test_existing_or_partial_recovery_not_adopted(self):
        D.seal(proof)
        before = (self.target / 'complete.json').stat().st_ino
        with self.assertRaises(FileExistsError):
            D.seal(proof)
        self.assertEqual((self.target / 'complete.json').stat().st_ino, before)

    def test_changed_authority_retains_intent_without_completion(self):
        first = proof()
        second = copy.deepcopy(first)
        second['tailAuthority']['generated'] = False
        with self.assertRaises(ValueError):
            D.seal(mock.Mock(side_effect=[first, second]))
        self.assertTrue((self.target / 'intent.json').exists())
        self.assertFalse((self.target / 'complete.json').exists())
        with self.assertRaises(FileExistsError):
            D.seal(proof)

    def verify(self, result=None):
        base = D.load('update-base')
        with mock.patch.object(base, 'inspect', return_value=result or proof()) as inspect, \
                mock.patch.object(D, 'load', return_value=base):
            value = D.verify('/root/original-rc2')
            inspect.assert_called_once_with('/root/original-rc2', _tail_token='b' * 12)
            return value

    def test_separate_recovered_inspection_is_readonly_and_reports31plus4_honestly(self):
        D.seal(proof)
        before = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in self.target.iterdir()}
        result = self.verify()
        self.assertTrue(result['installationCompletionVerified'])
        self.assertTrue(result['recoveryCertificateVerified'])
        self.assertFalse(result['originalCompletionWritten'])
        self.assertEqual(result['completedPhasesVerified'], 31)
        self.assertEqual(result['recoveredTailPhasesVerified'], 4)
        self.assertNotIn('tailAuthority', result)
        self.assertEqual(before, {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in self.target.iterdir()})

    def test_certificate_namespace_partial_wrong_mode_or_changed_authority_refused(self):
        D.seal(proof)
        (self.target / 'foreign').touch()
        with self.assertRaises(ValueError):
            self.verify()
        (self.target / 'foreign').unlink()
        self.target.chmod(0o755)
        with self.assertRaises(ValueError):
            self.verify()
        self.target.chmod(0o700)
        changed = proof()
        changed['tailAuthority']['generated'] = False
        with self.assertRaises(ValueError):
            self.verify(changed)
        (self.target / 'complete.json').unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_equal_bytes_receipt_replacement_during_live_proof_refused(self):
        D.seal(proof)
        base = D.load('update-base')
        def audit(*args, **kwargs):
            target = self.target / 'complete.json'
            temporary = self.target / 'temporary'
            temporary.write_bytes(target.read_bytes())
            temporary.chmod(0o600)
            temporary.replace(target)
            return proof()
        with mock.patch.object(base, 'inspect', side_effect=audit), mock.patch.object(D, 'load', return_value=base):
            with self.assertRaises(ValueError):
                D.verify('/root/original-rc2')

    def test_actual_process_death_leaves_no_false_completion_or_replay(self):
        code = '''import importlib.util,json,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('dead_tail',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
d.ROOT=Path(sys.argv[2]);n=0
def prove():
 global n
 n+=1
 if n==2:os._exit(73)
 return json.loads(sys.argv[3])
d.seal(prove)
'''
        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(self.target),
                                json.dumps(proof())], capture_output=True, timeout=20)
        self.assertEqual(child.returncode, 73, child.stderr.decode())
        self.assertTrue((self.target / 'intent.json').exists())
        self.assertFalse((self.target / 'complete.json').exists())
        with self.assertRaises(FileExistsError):
            D.seal(proof)


if __name__ == '__main__':
    unittest.main()
