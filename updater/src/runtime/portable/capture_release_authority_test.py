# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack, contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
ROOT=Path(__file__).parent
A=load('release_authority',ROOT/'capture-release-authority.py')
NATIVE=sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0


class SchemaTests(unittest.TestCase):
    def test_catalog_is_exact_and_malformed_diagnostics_are_generic(self):
        raw=(ROOT/'capture-release-verifier.json').read_bytes()
        self.assertEqual(len(A.catalog(raw)),99)
        for value in (None,b'generated-private-sentinel',raw+b' ',raw.replace(b'99cee2a',b'88cee2a',1)):
            with self.assertRaisesRegex(ValueError,'^Independent release verifier catalog rejected$'):A.catalog(value)

    def test_even_a_forged_support_flag_never_authorizes_an_undefined_component(self):
        for report in (None,{}, {'kind':'verified-installed-pilot-release','capturePolicyRuntimeSupported':False},
                       {'kind':'verified-installed-pilot-release','capturePolicyRuntimeSupported':True}):
            with self.assertRaises(ValueError):A.capture_binding(report)

    def wrapper(self,side='after',phases=None):
        state={'locked':False,'checks':0}
        @contextmanager
        def locked():
            self.assertFalse(state['locked']);state['locked']=True
            try:yield {'original':'enrolled-context'}
            finally:state['locked']=False
        def check(*args):self.assertTrue(state['locked']);state['checks']+=1
        tx=SimpleNamespace(digest='a'*64,after={'files':'original-owned'},value={'originalNamespace':{'original':'fixed-layout'}})
        tx.observe=lambda:(side,set(phases if phases is not None else ('intent.json','ready.json','apply-intent.json','applied.json','healthy.json')))
        tx.active=SimpleNamespace(metadata=check)
        manifest={'target':{'version':'0.1.1-rc.20','sourceSha256':'b'*64}}
        def signed(value):check();self.assertEqual(value,'a'*64);return {'authenticated':True},manifest,b'public-code',{'generation':[1,2,3]}
        wrapper=SimpleNamespace(inspect_enrollment=lambda:None,C=SimpleNamespace(locked=locked,records=lambda:[],current_view=lambda rows:tx),
                                B=SimpleNamespace(inspect_bootstrap=check,signed_input=signed),P=SimpleNamespace(encode=A.encode),
                                G=SimpleNamespace(protected_read=lambda *args:b'generated-config'))
        return wrapper,state

    def test_inspection_uses_shared_lock_and_does_not_infer_capture_admission(self):
        wrapper,state=self.wrapper();result=A.observed_release(wrapper)
        self.assertFalse(state['locked']);self.assertGreater(state['checks'],1)
        self.assertTrue(result['signedReleaseVerified'] and result['installedCodeOwnershipVerified'] and result['updateLockShared'])
        self.assertFalse(result['capturePolicyRuntimeSupported'] or result['readyForActivation'] or result['servicesChanged'])
        self.assertEqual(set(result['binding']),{'sourceSha256','configSha256','releaseSha256'})

    def test_pending_or_rollback_release_is_refused(self):
        for side,phases in (('before',None),('after',{'apply-intent.json','applied.json'}),('after',{'apply-intent.json','applied.json','healthy.json','rollback-request.json'})):
            wrapper,state=self.wrapper(side,phases)
            with self.assertRaisesRegex(ValueError,'Healthy accepted'):A.observed_release(wrapper)
            self.assertFalse(state['locked'])


@unittest.skipUnless(NATIVE,'Root Linux held-source and controller preflight tests')
class NativeTests(unittest.TestCase):
    def fixture(self):
        directory=tempfile.TemporaryDirectory(prefix='generated-release-code-');self.addCleanup(directory.cleanup)
        root=Path(directory.name);root.chmod(0o700);file=root/'entry.py';raw=b'value=7\n';file.write_bytes(raw);file.chmod(0o600)
        return root,file,{'entry.py':(len(raw),A.sha(raw))}

    def test_verified_held_bytes_load_and_unknown_import_is_refused(self):
        root,file,pins=self.fixture();original=importlib.util.spec_from_file_location
        with ExitStack() as stack:
            held=A.HeldCode(stack,root,pins)
            with A.verified_imports(held) as spec:
                definition=spec('generated',file);module=importlib.util.module_from_spec(definition);definition.loader.exec_module(module)
                self.assertEqual(module.value,7)
                with self.assertRaises(ValueError):spec('foreign',root/'foreign.py')
        self.assertIs(importlib.util.spec_from_file_location,original)

    def test_source_replacement_is_refused_before_execution(self):
        root,file,pins=self.fixture()
        with ExitStack() as stack:
            held=A.HeldCode(stack,root,pins)
            with A.verified_imports(held) as spec:
                definition=spec('generated',file);module=importlib.util.module_from_spec(definition)
                file.rename(root/'generated-old');file.write_bytes(b'value=99\n');file.chmod(0o600)
                with self.assertRaises(ValueError):definition.loader.exec_module(module)
                self.assertFalse(hasattr(module,'value'))

    def test_path_replacement_after_verified_read_cannot_execute_foreign_bytes(self):
        root,file,pins=self.fixture()
        with ExitStack() as stack:
            held=A.HeldCode(stack,root,pins);read=held.bytes
            def race(path):
                raw=read(path);file.rename(root/'generated-old');file.write_bytes(b'value=99\n');file.chmod(0o600);return raw
            held.bytes=race
            with A.verified_imports(held) as spec:
                definition=spec('generated',file);module=importlib.util.module_from_spec(definition)
                with self.assertRaises(ValueError):definition.loader.exec_module(module)
                self.assertEqual(module.value,7)

    def test_unsafe_source_mode_owner_and_links_are_rejected(self):
        for kind in ('mode','owner','link','symlink'):
            root,file,pins=self.fixture()
            if kind=='mode':file.chmod(0o666)
            if kind=='owner':os.chown(file,1000,1000)
            if kind=='link':os.link(file,root/'generated-second')
            if kind=='symlink':file.rename(root/'generated-old');file.symlink_to('generated-old')
            with ExitStack() as stack,self.assertRaises((ValueError,OSError)):A.HeldCode(stack,root,pins)

    def test_unsupported_release_is_denied_before_controller_lock_creation(self):
        P=load('policy',ROOT/'capture-policy-transaction.py');R=load('recycle',ROOT/'capture-video-recycle.py');C=load('controller',ROOT/'capture-policy-controller.py')
        root,file,pins=self.fixture();fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            def unsupported():return A.capture_binding({'kind':'verified-installed-pilot-release','capturePolicyRuntimeSupported':False})
            with self.assertRaises(ValueError),C.hold_controller(P,R,P.PolicyTransaction(fd),admit=unsupported,ready=lambda *args:True):pass
            self.assertFalse(os.path.lexists(root/C.LOCK));self.assertFalse(os.path.lexists(root/C.OPERATION));self.assertFalse(os.path.lexists(root/P.POLICY))
        finally:os.close(fd)


if __name__=='__main__':unittest.main()
