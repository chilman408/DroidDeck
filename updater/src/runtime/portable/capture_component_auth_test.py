# SPDX-License-Identifier: GPL-3.0-only
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('capture_auth_tested', Path(__file__).with_name('capture-component-auth.py'))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)
BUNDLE = os.environ.get('DROIDDECK_CAPTURE_COMPONENT_TEST_BUNDLE')
NATIVE = sys.platform == 'linux'


@unittest.skipUnless(BUNDLE, 'Explicit public candidate input required')
class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = Path(BUNDLE).read_bytes()
        cls.statement = C.statement(cls.bundle, 'a'*64)
        cls.value = C.P.document(cls.statement)

    def test_exact_reviewed_package_receives_staging_only_statement(self):
        report = C.review_signing_inputs(self.bundle, self.statement, 'a'*64)
        self.assertEqual(report['bundleSha256'], C.P.sha(self.bundle))
        self.assertEqual(self.value['compatibleReleaseSha256'], C.BASE_RELEASE)
        self.assertEqual(self.value['contractSha256'], C.P.sha(C.P.encode(C.C.CONTRACT)))
        self.assertIs(self.value['activationAllowed'], False)
        self.assertIs(report['payloadExecuted'], False)

    def test_statement_cannot_change_domain_base_source_manifest_or_authority(self):
        for key, changed in [('schema', True), ('kind', 'droiddeck-runtime-release'), ('channel', 'stable'),
                ('purpose', 'activate'), ('activationAllowed', True), ('activationAllowed', 0),
                ('keySha256', 'f'*64), ('bundleSha256', '0'*64), ('manifestSha256', '0'*64),
                ('sourceRevision', '0'*40), ('runtimeVersion', '0.1.1-rc.21'),
                ('compatibleReleaseSha256', '0'*64), ('contractSha256', '0'*64), ('extra', True)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.review_signing_inputs(self.bundle, C.P.encode({**self.value, key: changed}), 'a'*64)

    def test_missing_duplicate_noncanonical_and_extra_private_input_refused(self):
        value = dict(self.value); del value['purpose']
        for raw in (C.P.encode(value), self.statement.rstrip(), self.statement+b' ',
                    self.statement.replace(b'"schema":1', b'"schema":1,"schema":1')):
            with self.assertRaises(ValueError): C.review_signing_inputs(self.bundle, raw, 'a'*64)
        with self.assertRaises(ValueError): C.review_signing_inputs(self.bundle+b'\0', self.statement, 'a'*64)

    def test_missing_or_unenrolled_trust_fails_before_signature_work(self):
        for raw in (b'{}\n', C.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'stable', 'publicKeySpkiHex': '00'*44})):
            with patch.object(C.A, 'signature') as verify, self.assertRaises(ValueError):
                C.authenticate(self.bundle, self.statement, b'0'*64, raw)
            verify.assert_not_called()

    def test_legacy_release_domain_never_adopts_capture_statement(self):
        public = bytes.fromhex('302a300506032b6570032100') + b'\1'*32
        trust = C.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot', 'publicKeySpkiHex': public.hex()})
        raw = C.statement(self.bundle, C.P.sha(public))
        with patch.object(C.A, 'signature') as verify, self.assertRaises(ValueError):
            C.A.authenticate(self.bundle, raw, b'0'*64, trust)
        verify.assert_not_called()


@unittest.skipUnless(BUNDLE and NATIVE, 'Linux real public Ed25519 verification required')
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = Path(BUNDLE).read_bytes()
        # Generated fixture private material exists only inside Node RAM. Output
        # consists solely of public SPKI/trust/statement/signature, never a key.
        code = r'''
const c=require('node:crypto'),fs=require('node:fs');
const canon=o=>JSON.stringify(Object.fromEntries(Object.entries(o).sort(([a],[b])=>a<b?-1:a>b?1:0)))+'\n';
const v=JSON.parse(fs.readFileSync(0,'utf8')),p=c.generateKeyPairSync('ed25519');
const pub=p.publicKey.export({format:'der',type:'spki'});v.keySha256=c.createHash('sha256').update(pub).digest('hex');
const s=Buffer.from(canon(v)),trust=canon({schema:1,kind:'droiddeck-update-trust',channel:'pilot',publicKeySpkiHex:pub.toString('hex')});
process.stdout.write(JSON.stringify({statement:s.toString('base64'),trust:Buffer.from(trust).toString('base64'),signature:c.sign(null,s,p.privateKey).toString('base64')}));
'''
        fixture = json.loads(subprocess.check_output([shutil.which('node'), '-e', code], input=C.statement(cls.bundle, 'a'*64)))
        cls.statement, cls.trust, cls.signature = (base64.b64decode(fixture[n]) for n in ('statement', 'trust', 'signature'))

    def test_real_signature_authenticates_without_installation_or_activation_claim(self):
        report = C.authenticate(self.bundle, self.statement, self.signature, self.trust)
        self.assertIs(report['releaseStatementAuthenticated'], True)
        for key in ('originalInstallationOwnershipVerified', 'readyForActivation', 'servicesChanged', 'activationAllowed', 'payloadExecuted'):
            self.assertIs(report[key], False)

    def test_real_signature_tamper_and_truncation_refused(self):
        altered = bytes([self.signature[0]^1]) + self.signature[1:]
        for raw in (altered, self.signature[:-1], self.signature+b'0'):
            with self.assertRaises(ValueError): C.authenticate(self.bundle, self.statement, raw, self.trust)

    def test_other_enrolled_key_and_payload_tamper_refused(self):
        trust = C.P.document(self.trust); trust['publicKeySpkiHex'] = '302a300506032b6570032100' + '02'*32
        with self.assertRaises(ValueError): C.authenticate(self.bundle, self.statement, self.signature, C.P.encode(trust))
        with self.assertRaises(ValueError): C.authenticate(self.bundle+b'\0', self.statement, self.signature, self.trust)


if __name__ == '__main__': unittest.main(verbosity=2)
