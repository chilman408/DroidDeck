# SPDX-License-Identifier: GPL-3.0-only
import base64
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

def load(name):
    s=importlib.util.spec_from_file_location('bootstrap_test_'+name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
B=load('capture-control-bootstrap');A=load('capture-activation-auth')
FIXTURE=os.environ.get('DROIDDECK_CAPTURE_COMPONENT_TEST_BUNDLE')
NATIVE=sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0


@unittest.skipUnless(FIXTURE,'Explicit public component fixture required')
class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.component=Path(FIXTURE).read_bytes()
        cls.files={n:(Path(__file__).parents[2]/A.source_path(n)).read_bytes() for n in A.CONTROL_NAMES}
        cls.package=A.make_archive(cls.component,'a'*40,cls.files)
        code=r"""
const c=require('node:crypto'),f=require('node:fs'),v=JSON.parse(f.readFileSync(0,'utf8'));
const p=c.generateKeyPairSync('ed25519'),pub=p.publicKey.export({format:'der',type:'spki'});
const canon=v=>JSON.stringify(Object.fromEntries(Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0)))+'\n';
v.keySha256=c.createHash('sha256').update(pub).digest('hex');
const s=Buffer.from(canon(v)),trust=Buffer.from(canon({schema:1,kind:'droiddeck-update-trust',channel:'pilot',publicKeySpkiHex:pub.toString('hex')}));
process.stdout.write(JSON.stringify({statement:s.toString('base64'),signature:c.sign(null,s,p.privateKey).toString('base64'),trust:trust.toString('base64')}));
"""
        result=json.loads(subprocess.check_output([shutil.which('node'),'-e',code],input=A.statement(cls.package,'b'*64)))
        cls.statement,cls.signature,cls.trust=(base64.b64decode(result[k]) for k in ('statement','signature','trust'))
    def test_independent_loader_and_signed_recipe_have_identical_closed_contract(self):
        self.assertEqual(B.NAMES,A.CONTROL_NAMES);self.assertEqual(B.CONTRACT,A.CONTRACT)
        self.assertEqual({n:B.source(n) for n in B.NAMES},{n:A.source_path(n) for n in A.CONTROL_NAMES})
    def test_authentication_reads_no_executable_payload_before_crypto(self):
        with patch.object(B,'verify_signature') as verify:
            value,files=B.authenticate(self.package,self.statement,self.signature,self.trust)
        verify.assert_called_once()
        self.assertEqual(value['packageSha256'],B.sha(self.package))
        self.assertEqual(set(files),{A.source_path(n) for n in A.CONTROL_NAMES})
    def test_rejected_signature_cannot_reach_archive_or_module_loading(self):
        with patch.object(B,'verify_signature',side_effect=ValueError('generated signature rejection')),patch.object(tarfile,'open') as opened:
            with self.assertRaises(ValueError):B.authenticate(self.package,self.statement,self.signature,self.trust)
        opened.assert_not_called()
    def test_signed_python_bytes_are_never_executed_by_primitive_review(self):
        files={**self.files,'capture-activation-auth.py':self.files['capture-activation-auth.py']+b'\nraise AssertionError("generated execution sentinel")\n'}
        package=A.make_archive(self.component,'a'*40,files)
        value=A.P.document(A.statement(package,B.sha(B.public_key(self.trust))))
        with patch.object(B,'verify_signature'):
            _v,selected=B.authenticate(package,B.encode(value),self.signature,self.trust)
        self.assertIn(b'generated execution sentinel',selected['runtime/portable/capture-activation-auth.py'])
    def test_wrong_key_domain_flag_duplicate_contract_and_package_refused(self):
        value=B.document(self.statement)
        for key,v in [('schema',True),('kind','droiddeck-capture-runtime-attestation'),('activationAllowed',1),
            ('purpose','staging-only'),('keySha256','0'*64),('contractSha256','0'*64),('extra',True)]:
            with patch.object(B,'verify_signature') as verify,self.assertRaises(ValueError):
                B.authenticate(self.package,B.encode({**value,key:v}),self.signature,self.trust)
            verify.assert_not_called()
        with self.assertRaises(ValueError):B.authenticate(self.package,self.statement.replace(b'"schema":1',b'"schema":1,"schema":1'),self.signature,self.trust)
        with patch.object(B,'verify_signature') as verify,self.assertRaises(ValueError):
            B.authenticate(self.package+b'\0',self.statement,self.signature,self.trust)
        verify.assert_not_called()
    def test_missing_extra_changed_noncanonical_archive_and_version_refused(self):
        original=A.unpack(self.package)
        for transform in (
            lambda m:m.pop(A.PREFIX+'control/preview-gateway/release-version.json'),
            lambda m:m.update({A.PREFIX+'control/foreign.py':(b'pass\n',0o644)}),
            lambda m:m.update({A.PREFIX+'control/capture-video-admission.py':(b'pass\n',0o644)}),
            lambda m:m.update({A.PREFIX+'../foreign.py':(b'pass\n',0o644)})):
            members=dict(original);transform(members);raw=A.P.canonical_archive(members)
            statement=B.encode({**B.document(self.statement),'packageSha256':B.sha(raw)})
            with patch.object(B,'verify_signature'),self.assertRaises(ValueError):
                B.authenticate(raw,statement,self.signature,self.trust)
    def test_enrollment_is_public_closed_canonical_ed25519_only(self):
        for raw in (b'{}\n',self.trust.rstrip(),B.encode({**B.document(self.trust),'channel':'stable'}),
            B.encode({**B.document(self.trust),'publicKeySpkiHex':'00'*44})):
            with self.assertRaises(ValueError):B.public_key(raw)


@unittest.skipUnless(FIXTURE and NATIVE,'Protected native loader metadata and real Ed25519 required')
class Owned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Policy.setUpClass()
        for name in ('package','statement','signature','trust'):setattr(cls,name,getattr(Policy,name))
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='capture-control-loader-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.inputs=self.root/'inputs';self.inputs.mkdir(mode=0o700)
        for name,data in [('package.tar',self.package),('statement.json',self.statement),('signature.bin',self.signature),('trust.json',self.trust)]:
            p=self.inputs/name;p.write_bytes(data);p.chmod(0o600)
        self.patch_root=patch.object(B,'ROOT',self.root/'controls');self.patch_trust=patch.object(B,'TRUST',self.inputs/'trust.json')
        self.patch_root.start();self.patch_trust.start();self.addCleanup(self.patch_root.stop);self.addCleanup(self.patch_trust.stop)
        self.digest=B.sha(self.package);self.slot=B.ROOT/self.digest
    def stage(self):
        return B.stage(self.inputs/'package.tar',self.inputs/'statement.json',self.inputs/'signature.bin',self.digest)
    def test_real_signature_stage_and_held_import_have_exact_owned_closure(self):
        result=self.stage();self.assertEqual(result['controlFiles'],21);self.assertFalse(result['servicesChanged'])
        with B.hold_controls(self.digest) as held:
            self.assertTrue(held['report']['activationStatementAuthenticated'])
            self.assertTrue(held['check']())
            module=held['module']('capture-view-controller.py')
            self.assertEqual(str(module.ROOT),'/var/lib/droiddeck-capture-switch')
            with self.assertRaises(ValueError):held['module']('../foreign.py')
        with self.assertRaises(FileExistsError):self.stage()
    def test_signature_tamper_fails_before_creating_a_control_namespace(self):
        p=self.inputs/'signature.bin';raw=p.read_bytes();p.write_bytes(bytes([raw[0]^1])+raw[1:])
        with self.assertRaises(ValueError):self.stage()
        self.assertFalse(B.ROOT.exists())
    def test_incomplete_stage_is_retained_refused_and_never_replayed(self):
        with patch.object(B,'load_auth',side_effect=RuntimeError('generated interrupted verifier')):
            with self.assertRaises(RuntimeError):self.stage()
        self.assertTrue(self.slot.exists());self.assertFalse((self.slot/'complete.json').exists())
        with self.assertRaises(ValueError),B.hold_controls(self.digest):pass
        with self.assertRaises(FileExistsError):self.stage()
    def enrollment(self):
        parent=self.root/'loader';parent.mkdir(mode=0o700)
        program=parent/'bootstrap.py';program.write_bytes(b'generated nonexecuted loader bytes');program.chmod(0o600)
        service=self.root/'recovery.service';service.write_bytes(b'generated service fixture');service.chmod(0o644)
        receipt={'schema':1,'kind':'operator-pinned-capture-loader','sourceRevision':'a'*40,
            'files':{'bootstrap.py':{'sha256':B.sha(program.read_bytes()),'generation':B.generation(program.lstat())},
                'recoveryTemplate':{'sha256':B.sha(service.read_bytes()),'generation':B.generation(service.lstat())}}}
        complete=parent/'complete.json';complete.write_bytes(B.encode(receipt));complete.chmod(0o600)
        return program,service
    def test_operator_pinned_loader_and_service_remain_held_during_operation(self):
        program,service=self.enrollment()
        with patch.object(B,'SELF',program),patch.object(B,'SERVICE',service),patch.object(B,'__file__',str(program)):
            with self.assertRaises(ValueError):
                with B.hold_self() as check:
                    check();raw=service.read_bytes();service.rename(service.with_suffix('.prior'));service.write_bytes(raw);service.chmod(0o644)
                    with self.assertRaises(ValueError):check()
    def test_self_enrollment_rejects_copy_sidecar_and_changed_generation(self):
        program,service=self.enrollment()
        with patch.object(B,'SELF',program),patch.object(B,'SERVICE',service):
            with self.assertRaises(ValueError),B.hold_self():pass
            with patch.object(B,'__file__',str(program)):
                program.chmod(0o644)
                with self.assertRaises(ValueError),B.hold_self():pass
                program.chmod(0o600)
                (program.parent/'foreign.py').write_bytes(b'pass')
                with self.assertRaises(ValueError),B.hold_self():pass
    def test_same_byte_replacement_and_held_metadata_changes_are_refused(self):
        self.stage();file=self.slot/'runtime/portable/capture-video-admission.py'
        with self.assertRaises(ValueError):
            with B.hold_controls(self.digest) as held:
                raw=file.read_bytes();file.rename(file.with_suffix('.old'));file.write_bytes(raw);file.chmod(0o600)
                with self.assertRaises(ValueError):held['check']()
                self.assertFalse(file.stat().st_ino==file.with_suffix('.old').stat().st_ino)


if __name__=='__main__':unittest.main(verbosity=2)
