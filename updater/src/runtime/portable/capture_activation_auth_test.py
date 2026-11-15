# SPDX-License-Identifier: GPL-3.0-only
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

s=importlib.util.spec_from_file_location('activation_test',Path(__file__).with_name('capture-activation-auth.py'))
A=importlib.util.module_from_spec(s);s.loader.exec_module(A)
BUNDLE=os.environ.get('DROIDDECK_CAPTURE_COMPONENT_TEST_BUNDLE')


@unittest.skipUnless(BUNDLE,'Explicit public component fixture required')
class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.component=Path(BUNDLE).read_bytes()
        cls.files={n:(Path(__file__).parents[2]/A.source_path(n)).read_bytes() for n in A.CONTROL_NAMES}
        cls.package=A.make_archive(cls.component,'a'*40,cls.files)
        cls.statement=A.statement(cls.package,'b'*64)
        cls.value=A.P.document(cls.statement)
    def changed(self,transform):
        members=A.unpack(self.package);transform(members);return A.P.canonical_archive(members)
    def test_complete_control_and_runtime_binding_without_execution(self):
        report=A.review(self.package,A.P.sha(self.package))
        self.assertEqual(report['controlFiles'],21)
        self.assertEqual(report['componentSha256'],A.P.sha(self.component))
        self.assertFalse(report['payloadExecuted'] or report['servicesChanged'] or report['readyForActivation'])
        self.assertEqual(A.review_signing_inputs(self.package,self.statement,'b'*64)['statementSha256'],A.P.sha(self.statement))
    def test_required_version_asset_and_closed_source_map(self):
        missing=dict(self.files);missing.pop('preview-gateway/release-version.json')
        with self.assertRaises(ValueError):A.make_archive(self.component,'a'*40,missing)
        changed={**self.files,'preview-gateway/release-version.json':A.P.encode({'version':'0.1.1-rc.21'})}
        with self.assertRaises(ValueError):A.make_archive(self.component,'a'*40,changed)
        self.assertEqual(A.source_path('preview-gateway/release-version.json'),'runtime/preview-gateway/release-version.json')
        for name in ('../release-version.json','credentials.json','preview-gateway/../portable/update-target.py'):
            with self.assertRaises(ValueError):A.source_path(name)
    def test_deterministic_archive_with_no_machine_or_private_inputs(self):
        self.assertEqual(self.package,A.make_archive(self.component,'a'*40,dict(reversed(list(self.files.items())))))
        with self.assertRaises(ValueError):A.make_archive(self.component,'a'*40,{**self.files,'key.pem':b'fixture'})
    def test_different_source_controls_and_component_reject_old_statement(self):
        changed=dict(self.files);changed['capture-video-admission.py']+=b'\n# generated change\n'
        for raw in (A.make_archive(self.component,'c'*40,self.files),A.make_archive(self.component,'a'*40,changed)):
            with self.assertRaises(ValueError):A.review_signing_inputs(raw,self.statement,'b'*64)
        with self.assertRaises(ValueError):A.make_archive(self.component+b'\0','a'*40,self.files)
    def test_missing_extra_link_and_noncanonical_members_refused(self):
        for change in (
            lambda m:m.pop(A.PREFIX+'control/effective-units.py'),
            lambda m:m.update({A.PREFIX+'control/foreign.py':(b'pass\n',0o644)}),
            lambda m:m.update({A.PREFIX+'../foreign.py':(b'pass\n',0o644)}),
            lambda m:m.update({A.PREFIX+'control/effective-units.py':(b'pass\n',0o755)})):
            raw=self.changed(change)
            with self.assertRaises(ValueError):A.review(raw,A.P.sha(raw))
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as t:
            info=tarfile.TarInfo(A.COMPONENT);info.type=tarfile.SYMTYPE;info.linkname='/generated';t.addfile(info)
        with self.assertRaises(ValueError):A.review(out.getvalue(),A.P.sha(out.getvalue()))
        with self.assertRaises(ValueError):A.review(self.package+b'\0',A.P.sha(self.package+b'\0'))
    def test_changed_bytes_and_unsafe_contract_refused(self):
        cases=[lambda m:m.update({A.PREFIX+'control/effective-units.py':(b'pass\n',0o644)})]
        for contract in ({**A.CONTRACT,'unraidChanges':True},{**A.CONTRACT,'healthyExpiry':True},{**A.CONTRACT,'heldNativeSource':'optional'}):
            def change(m,c=contract):
                doc=A.P.document(m[A.MANIFEST][0]);doc['contract']=c;m[A.MANIFEST]=(A.P.encode(doc),0o644)
            cases.append(change)
        for change in cases:
            raw=self.changed(change)
            with self.assertRaises(ValueError):A.review(raw,A.P.sha(raw))
    def test_closed_signature_domain_base_key_and_digests(self):
        for key,value in [('schema',True),('kind',A.C.KIND),('channel','stable'),('purpose','staging-only'),
            ('activationAllowed',False),('activationAllowed',1),('keySha256','c'*64),('packageSha256','0'*64),
            ('manifestSha256','0'*64),('componentSha256','0'*64),('controlSourcesSha256','0'*64),
            ('sourceRevision','0'*40),('compatibleReleaseSha256','0'*64),('contractSha256','0'*64),('extra',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                A.review_signing_inputs(self.package,A.P.encode({**self.value,key:value}),'b'*64)
    def test_duplicate_missing_noncanonical_json_refused(self):
        value=dict(self.value);del value['purpose']
        for raw in (self.statement.rstrip(),self.statement+b' ',A.P.encode(value),
            self.statement.replace(b'"schema":1',b'"schema":1,"schema":1')):
            with self.assertRaises(ValueError):A.review_signing_inputs(self.package,raw,'b'*64)
    def test_staging_and_legacy_signatures_never_adopt_activation(self):
        public=bytes.fromhex('302a300506032b6570032100')+b'\1'*32
        trust=A.P.encode({'schema':1,'kind':'droiddeck-update-trust','channel':'pilot','publicKeySpkiHex':public.hex()})
        for verifier,bundle in ((A.C,self.component),(A.C.A,self.package)):
            with patch.object(verifier.A if verifier is A.C else verifier,'signature') as verify,self.assertRaises(ValueError):
                verifier.authenticate(bundle,self.statement,b'0'*64,trust)
            verify.assert_not_called()
        staging=A.C.statement(self.component,A.P.sha(public))
        with patch.object(A.C.A,'signature') as verify,self.assertRaises(ValueError):
            A.authenticate(self.package,staging,b'0'*64,trust)
        verify.assert_not_called()
    def test_git_source_is_committed_regular_bytes_not_mutable_checkout(self):
        with tempfile.TemporaryDirectory(prefix='activation-git-') as temp:
            root=Path(temp)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init','-q');git('config','core.autocrlf','false');git('config','user.name','Generated Fixture');git('config','user.email','fixture@example.invalid')
            target=root/'runtime/portable';target.mkdir(parents=True)
            for name,raw in self.files.items():
                dest=root/A.source_path(name);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            git('add','.');git('commit','-qm','Generated source fixture')
            commit=git('rev-parse','HEAD').decode().strip()
            (target/'capture-video-admission.py').write_bytes(b'raise RuntimeError("uncommitted fixture")\n')
            (target/'credentials.json').write_bytes(b'generated untracked sentinel')
            self.assertEqual(A.selected_controls(str(root),commit),self.files)
            with self.assertRaises(ValueError):A.selected_controls(str(root),'--all')
    def test_git_symlink_mode_cannot_be_packaged_as_control_code(self):
        with tempfile.TemporaryDirectory(prefix='activation-git-mode-') as temp:
            root=Path(temp)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init','-q');git('config','core.autocrlf','false');git('config','user.name','Generated Fixture');git('config','user.email','fixture@example.invalid')
            target=root/'runtime/portable';target.mkdir(parents=True)
            for name,raw in self.files.items():
                dest=root/A.source_path(name);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            git('add','.');git('commit','-qm','Generated original fixture')
            name='runtime/portable/capture-video-admission.py'
            obj=git('rev-parse','HEAD:'+name).decode().strip()
            git('update-index','--cacheinfo','120000',obj,name);git('commit','-qm','Generated linked control fixture')
            with self.assertRaises(ValueError):A.selected_controls(str(root),git('rev-parse','HEAD').decode().strip())
    def test_bad_enrollment_refused_before_crypto(self):
        with patch.object(A.C.A,'signature') as verify,self.assertRaises(ValueError):
            A.authenticate(self.package,self.statement,b'0'*64,b'{}\n')
        verify.assert_not_called()


@unittest.skipUnless(BUNDLE and sys.platform=='linux','Native public Ed25519 verification required')
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.component=Path(BUNDLE).read_bytes()
        cls.package=A.make_archive(cls.component,'a'*40,{n:(Path(__file__).parents[2]/A.source_path(n)).read_bytes() for n in A.CONTROL_NAMES})
        code=r"""
const c=require('node:crypto'),f=require('node:fs'),v=JSON.parse(f.readFileSync(0,'utf8'));
const p=c.generateKeyPairSync('ed25519'),pub=p.publicKey.export({format:'der',type:'spki'});
const canon=v=>JSON.stringify(Object.fromEntries(Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0)))+'\n';
v.keySha256=c.createHash('sha256').update(pub).digest('hex');
const s=Buffer.from(canon(v)),trust=Buffer.from(canon({schema:1,kind:'droiddeck-update-trust',channel:'pilot',publicKeySpkiHex:pub.toString('hex')}));
process.stdout.write(JSON.stringify({statement:s.toString('base64'),signature:c.sign(null,s,p.privateKey).toString('base64'),trust:trust.toString('base64')}));
"""
        value=json.loads(subprocess.check_output([shutil.which('node'),'-e',code],input=A.statement(cls.package,'b'*64)))
        cls.statement,cls.signature,cls.trust=(base64.b64decode(value[n]) for n in ('statement','signature','trust'))
    def test_real_signature_authenticates_without_live_admission_claim(self):
        report=A.authenticate(self.package,self.statement,self.signature,self.trust)
        self.assertTrue(report['activationStatementAuthenticated'])
        for key in ('payloadExecuted','servicesChanged','liveInstallationOwnershipVerified','effectiveGraphVerified','nativeLeaseVerified','readyForActivation'):
            self.assertFalse(report[key])
    def test_signature_bitflip_truncation_other_key_and_payload_refused(self):
        for signature in (bytes([self.signature[0]^1])+self.signature[1:],self.signature[:-1],self.signature+b'0'):
            with self.assertRaises(ValueError):A.authenticate(self.package,self.statement,signature,self.trust)
        trust=A.P.document(self.trust);trust['publicKeySpkiHex']='302a300506032b6570032100'+'02'*32
        with self.assertRaises(ValueError):A.authenticate(self.package,self.statement,self.signature,A.P.encode(trust))
        with self.assertRaises(ValueError):A.authenticate(self.package+b'\0',self.statement,self.signature,self.trust)


if __name__=='__main__':unittest.main(verbosity=2)
