# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('admission_tested', Path(__file__).with_name('update-admission.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def fixture():
    from update_package_test import fixture as package_fixture, bundle
    manifest, _payloads = package_fixture()
    components = [{'name': name, 'root': root, 'files': copy.deepcopy(manifest['components'][0]['before'])}
                  for name, root in sorted(D.P.COMPONENTS.items())]
    proof = {'kind': 'installed-update-base-inspection', 'base': dict(D.P.BASE),
             'originalInstallationOwnershipVerified': True, 'completedPhasesVerified': 35,
             'components': components, 'objects': {'generated': {'inode': 3}},
             'receiptGenerations': {'/generated/receipt': [1, 2, 3]}, 'readOnly': True, 'servicesChanged': False}
    return bundle(), manifest, proof


def statement(manifest, raw, key):
    return D.P.encode({'schema': 1, 'kind': 'droiddeck-runtime-release', 'channel': 'pilot',
                       'keySha256': D.P.sha(key), 'bundleSha256': D.P.sha(raw),
                       'manifestSha256': D.P.sha(D.P.encode(manifest)), 'base': manifest['base'],
                       'target': manifest['target'], 'platform': manifest['platform'], 'dataPolicy': 'preserve'})


class Policy(unittest.TestCase):
    def setUp(self):
        self.raw, self.manifest, self.proof = fixture()
        self.key = D.SPKI + bytes(range(32))
        self.trust = D.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot',
                                 'publicKeySpkiHex': self.key.hex()})
        self.statement = statement(self.manifest, self.raw, self.key)

    def test_public_only_canonical_ed25519_trust(self):
        self.assertEqual(D.trust(self.trust), self.key)
        value = D.P.document(self.trust)
        for field, changed in (('schema', True), ('channel', 'stable'), ('kind', 'private-key'),
                               ('publicKeySpkiHex', self.key.hex().upper()), ('extra', 'command')):
            with self.assertRaises(ValueError):
                D.trust(D.P.encode({**value, field: changed}))
        for raw in (b'{}\n', b'-----BEGIN PRIVATE KEY-----', self.trust + b' '):
            with self.assertRaises(ValueError):
                D.trust(raw)

    def test_statement_binds_exact_base_target_bundle_manifest_and_channel(self):
        with mock.patch.object(D, 'signature') as verify:
            manifest, review, key = D.authenticate(self.raw, self.statement, b's' * 64, self.trust)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(key, D.P.sha(self.key))
        self.assertFalse(review['installable'])
        verify.assert_called_once_with(self.key, self.statement, b's' * 64)

    def test_wrong_signed_metadata_or_payload_refused_even_if_signature_passes(self):
        value = D.P.document(self.statement)
        for field, changed in (('target', {**value['target'], 'sourceRevision': 'f' * 40}), ('base', {}),
                               ('platform', {}), ('manifestSha256', 'c' * 64), ('bundleSha256', 'c' * 64),
                               ('keySha256', 'c' * 64), ('dataPolicy', 'migrate'), ('channel', 'stable'),
                               ('kind', 'other-product'), ('schema', True), ('extra', True)):
            with mock.patch.object(D, 'signature'), self.assertRaises(ValueError):
                D.authenticate(self.raw, D.P.encode({**value, field: changed}), b's' * 64, self.trust)

    def test_signature_length_refuses_before_verifier(self):
        for signed in (b'', b's' * 63, b's' * 65):
            with mock.patch.object(D.subprocess, 'run') as run, self.assertRaises(ValueError):
                D.signature(self.key, self.statement, signed)
            run.assert_not_called()

    def test_before_inventory_equals_entire_original_component(self):
        D.bind(self.manifest, self.proof, 'completed')
        for changed in ([], self.proof['components'][1]['files'][:1]):
            proof = copy.deepcopy(self.proof)
            proof['components'][1]['files'] = changed
            with self.assertRaises(ValueError):
                D.bind(self.manifest, proof, 'completed')

    def test_unsealed_recovery_or_missing_ownership_refused(self):
        for field, changed in (('kind', 'installation-tail-audit'), ('originalInstallationOwnershipVerified', False),
                               ('readOnly', False), ('servicesChanged', True), ('objects', {}),
                               ('receiptGenerations', {}), ('completedPhasesVerified', 31), ('components', [])):
            with self.assertRaises(ValueError):
                D.bind(self.manifest, {**self.proof, field: changed}, 'completed')
        with self.assertRaises(ValueError):
            D.bind(self.manifest, self.proof, 'recovered')
        recovered = {**self.proof, 'installationCompletionVerified': True, 'recoveryCertificateVerified': True,
                     'completionAuthority': 'separate-verified-tail-recovery', 'completedPhasesVerified': 31,
                     'recoveredTailPhasesVerified': 4}
        D.bind(self.manifest, recovered, 'recovered')

    def test_changed_receipt_generation_changes_binding(self):
        changed = copy.deepcopy(self.proof)
        changed['receiptGenerations']['/generated/receipt'][1] = 4
        self.assertNotEqual(D.bind(self.manifest, self.proof, 'completed'), D.bind(self.manifest, changed, 'completed'))

    def test_cli_has_no_apply_resume_or_trust_enrollment_mode(self):
        for args in ([], ['apply'], ['enroll-key'], ['admit', 'auto'], ['resume', 'a' * 64]):
            with mock.patch.object(D, 'admit') as admit, self.assertRaises(ValueError):
                D.main(args)
            admit.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated admission fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-admission-fixture-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw, self.manifest, self.proof = fixture()
        self.private = self.root / 'fixture-signing-key.pem'
        subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.private)],
                       check=True, capture_output=True, timeout=15)
        self.key = subprocess.check_output(['/usr/bin/openssl', 'pkey', '-in', str(self.private), '-pubout', '-outform', 'DER'], timeout=15)
        self.trust = self.root / 'trust.json'
        self.trust.write_bytes(D.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot',
                                          'publicKeySpkiHex': self.key.hex()}))
        self.bundle = self.root / 'input.tar'
        self.bundle.write_bytes(self.raw)
        self.statement = self.root / 'release.json'
        self.statement.write_bytes(statement(self.manifest, self.raw, self.key))
        self.signed = self.root / 'release.sig'
        subprocess.run(['/usr/bin/openssl', 'pkeyutl', '-sign', '-inkey', str(self.private), '-rawin',
                        '-in', str(self.statement), '-out', str(self.signed)], check=True, capture_output=True, timeout=15)
        self.machine = self.root / 'machine-id'
        self.machine.write_bytes(b'a' * 32 + b'\n')
        for path in self.root.iterdir():
            path.chmod(0o600)
        self.machine.chmod(0o444)
        for name, value in (('ROOT', self.root / 'admissions'), ('TRUST', self.trust), ('MACHINE', self.machine)):
            patch = mock.patch.object(D, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(D, 'base_proof', side_effect=lambda *_: copy.deepcopy(self.proof))
        self.prove = patch.start()
        self.addCleanup(patch.stop)
        self.package = '/root/generated-original-package'

    def admit(self):
        return D.admit('completed', self.package, self.bundle, self.statement, self.signed)

    def inspect(self):
        return D.inspect(D.P.sha(self.raw), self.package)

    def directory(self):
        return D.ROOT / D.P.sha(self.raw)

    def test_real_signature_verification_and_tamper_or_wrong_key_refusal(self):
        raw = self.statement.read_bytes()
        D.signature(self.key, raw, self.signed.read_bytes())
        for key, document, signed in ((self.key, raw + b' ', self.signed.read_bytes()),
                                      (self.key, raw, b'x' * 64), (D.SPKI + b'z' * 32, raw, self.signed.read_bytes())):
            with self.assertRaises(ValueError):
                D.signature(key, document, signed)

    def test_durable_admission_then_readonly_inspect_never_executes_payload(self):
        created = self.admit()
        self.assertTrue(created['durableAdmissionCreated'])
        self.assertTrue(created['releaseStatementAuthenticated'])
        for key in ('installable', 'targetArtifactsReproduced', 'independentRecoveryHandoffVerified',
                    'readyForApply', 'payloadExecuted', 'servicesChanged'):
            self.assertFalse(created[key])
        self.assertEqual(self.prove.call_count, 2)
        before = {path.name: D.P.identity(path.stat()) for path in self.directory().iterdir()}
        inspected = self.inspect()
        self.assertTrue(inspected['readOnly'])
        self.assertFalse(inspected['durableAdmissionCreated'])
        self.assertEqual(before, {path.name: D.P.identity(path.stat()) for path in self.directory().iterdir()})
        self.assertEqual(set(before), D.FILES)
        self.assertEqual(self.directory().stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.directory().iterdir()))

    def test_no_trust_refuses_before_payload_or_journal_access(self):
        self.trust.unlink()
        self.bundle.unlink()
        with self.assertRaises(FileNotFoundError) as error:
            self.admit()
        self.assertIn('trust.json', str(error.exception))
        self.assertFalse(D.ROOT.exists())

    def test_bad_signature_never_creates_admission_or_proves_base(self):
        self.signed.write_bytes(b'x' * 64)
        with self.assertRaises(ValueError):
            self.admit()
        self.assertFalse(D.ROOT.exists())
        self.assertEqual(self.prove.call_count, 0)

    def test_same_update_never_replayed_or_adopted(self):
        self.admit()
        before = (self.directory() / 'intent.json').stat().st_ino
        with self.assertRaises(FileExistsError):
            self.admit()
        self.assertEqual((self.directory() / 'intent.json').stat().st_ino, before)

    def test_base_change_retains_partial_admission_without_replay(self):
        second = copy.deepcopy(self.proof)
        second['receiptGenerations']['/generated/receipt'][1] = 9
        self.prove.side_effect = [copy.deepcopy(self.proof), second]
        with self.assertRaises(ValueError):
            self.admit()
        self.assertTrue((self.directory() / 'intent.json').exists())
        self.assertFalse((self.directory() / 'complete.json').exists())
        self.prove.side_effect = lambda *_: copy.deepcopy(self.proof)
        with self.assertRaises(FileExistsError):
            self.admit()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_same_bytes_payload_replacement_not_adopted(self):
        self.admit()
        target = self.directory() / 'bundle.tar'
        replacement = self.root / 'replacement'
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(target)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_changed_machine_or_base_refuses_inspection(self):
        self.admit()
        self.machine.write_bytes(b'b' * 32 + b'\n')
        with self.assertRaises(ValueError):
            self.inspect()
        self.machine.write_bytes(b'a' * 32 + b'\n')
        self.proof['receiptGenerations']['/generated/receipt'][1] = 12
        with self.assertRaises(ValueError):
            self.inspect()

    def test_changed_trust_or_unknown_admission_object_refused(self):
        self.admit()
        original = self.trust.read_bytes()
        self.trust.write_bytes(original + b' ')
        with self.assertRaises(ValueError):
            self.inspect()
        self.trust.write_bytes(original)
        (self.directory() / 'unexpected').touch()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_recovered_mode_is_explicit_and_requires_sealed_recovery_proof(self):
        self.proof.update(installationCompletionVerified=True, recoveryCertificateVerified=True,
                          completionAuthority='separate-verified-tail-recovery', completedPhasesVerified=31,
                          recoveredTailPhasesVerified=4)
        result = D.admit('recovered', self.package, self.bundle, self.statement, self.signed)
        self.assertTrue(result['durableAdmissionCreated'])
        self.assertTrue(self.inspect()['readOnly'])
        self.assertTrue(all(call.args[0] == 'recovered' for call in self.prove.call_args_list))

    def test_namespace_replacement_or_foreign_root_mode_refused(self):
        self.admit()
        old = self.directory()
        moved = self.root / 'displaced'
        old.rename(moved)
        old.mkdir(mode=0o700)
        for path in moved.iterdir():
            (old / path.name).write_bytes(path.read_bytes())
            (old / path.name).chmod(0o600)
        with self.assertRaises(ValueError):
            self.inspect()
        D.ROOT.chmod(0o755)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_symlink_hardlink_or_public_input_refused(self):
        original = self.bundle.read_bytes()
        self.bundle.unlink()
        self.bundle.symlink_to(self.statement)
        with self.assertRaises(OSError):
            self.admit()
        self.bundle.unlink()
        self.bundle.write_bytes(original)
        self.bundle.chmod(0o600)
        hardlink = self.root / 'hardlink'
        os.link(self.bundle, hardlink)
        with self.assertRaises(ValueError):
            self.admit()
        hardlink.unlink()
        self.bundle.chmod(0o644)
        with self.assertRaises(ValueError):
            self.admit()
        self.assertFalse(D.ROOT.exists())

    def test_process_death_after_intent_has_no_false_completion_or_replay(self):
        code = '''import importlib.util,json,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('dead_admission',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
r=Path(sys.argv[2]);d.ROOT=r/'admissions';d.TRUST=r/'trust.json';d.MACHINE=r/'machine-id'
d.base_proof=lambda *args:json.loads(sys.argv[3])
b=d.load('update-base');original=b.H.write_new
def write(fd,name,raw):
 original(fd,name,raw)
 if name=='intent.json':os._exit(73)
real_load=d.load
d.load=lambda name:b if name=='update-base' else real_load(name)
b.H.write_new=write
d.admit('completed','/root/generated-original-package',r/'input.tar',r/'release.json',r/'release.sig')
'''
        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(self.root),
                                json.dumps(self.proof)], capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 73, child.stderr.decode())
        self.assertEqual(set(path.name for path in self.directory().iterdir()), {'intent.json'})
        with self.assertRaises(FileExistsError):
            self.admit()
        with self.assertRaises(ValueError):
            self.inspect()


if __name__ == '__main__':
    unittest.main()
