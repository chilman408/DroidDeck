# SPDX-License-Identifier: GPL-3.0-only
"""Generated Linux private-view ownership tests; explicit component lease fixture."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

NATIVE=sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0

@unittest.skipUnless(sys.platform=='linux','Linux control imports required')
class Policies(unittest.TestCase):
    def test_new_gpu_view_requests_encoder_and_selective_gpu_preprocessing(self):
        spec=importlib.util.spec_from_file_location('view_policy_tested',Path(__file__).with_name('capture-private-view.py'))
        V=importlib.util.module_from_spec(spec);spec.loader.exec_module(V)
        self.assertEqual(V.policy('gpu'),V.P.encode({'version':2,'preprocessing':'gpu','encoder':'vaapi'}))
        self.assertEqual(V.policy('cpu'),V.P.encode({'version':1,'preprocessing':'cpu'}))
        self.assertIsNone(V.policy('default'))


@unittest.skipUnless(NATIVE,'Root Linux private-view filesystem tests required')
class Views(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('view_tested',Path(__file__).with_name('capture-private-view.py'))
        cls.V=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.V)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='generated-capture-view-',dir='/run');self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);root.chmod(0o700)
        self.config=root/'runtime.json';self.config.write_bytes(b'{"generated":"nonexecuted config fixture"}\n');self.config.chmod(0o644)
        self.digest='a'*64;self.token='b'*32;self.calls=0
        self.component={'report':{'bundleSha256':self.digest},'runtimeRoot':'/var/lib/droiddeck-capture/'+self.digest+'/runtime',
            'receiptSha256':'c'*64,'baseBinding':{'configSha256':self.V.P.sha(self.config.read_bytes()),'sourceSha256':'d'*64,'releaseSha256':'e'*64}}
        def check():self.calls+=1
        self.component['check']=check
        @contextmanager
        def component(digest):
            if digest!=self.digest:raise ValueError('Generated component digest mismatch')
            yield self.component
            check()
        self.addCleanup(patch.stopall)
        patch.object(self.V,'ROOT',root/'views').start()
        patch.object(self.V,'CONFIG',self.config).start()
        patch.object(self.V.S,'hold_component',component).start()

    def test_default_and_explicit_cpu_gpu_views_copy_exact_configuration(self):
        for index,mode in enumerate(('default','cpu','gpu')):
            token=str(index+1)*32
            result=self.V.prepare(self.digest,token,mode)
            self.assertIs(result['activationAllowed'],False)
            self.assertEqual((self.V.ROOT/token/'config/runtime.json').read_bytes(),self.config.read_bytes())
            p=self.V.ROOT/token/'config/capture-policy.json'
            self.assertEqual(p.read_bytes() if p.exists() else None,self.V.policy(mode))
            self.assertEqual(self.V.inspect(token)['mode'],mode)
        self.assertGreater(self.calls,5)

    def test_mount_plan_covers_entire_runtime_and_config_without_command_reset(self):
        self.V.prepare(self.digest,self.token)
        with self.V.hold_view(self.token) as view:
            text=view['mapping'].decode()
            self.assertEqual(text.count('BindReadOnlyPaths='),2)
            self.assertIn(':/opt/droiddeck/runtime\n',text);self.assertIn(':/etc/droiddeck\n',text)
            self.assertIn('PrivateMounts=yes\n',text);self.assertNotIn('ExecStart',text)
            self.assertNotIn('Environment',text);self.assertNotIn('BindReadOnlyPaths=\n',text)
            view['check']()

    def test_foreign_same_byte_replacement_and_extra_configuration_refused(self):
        self.V.prepare(self.digest,self.token)
        p=self.V.ROOT/self.token/'config/runtime.json';raw=p.read_bytes();p.unlink();p.write_bytes(raw);p.chmod(0o644)
        with self.assertRaises(ValueError):self.V.inspect(self.token)

    def test_foreign_policy_or_link_in_default_view_refused(self):
        self.V.prepare(self.digest,self.token)
        p=self.V.ROOT/self.token/'config/capture-policy.json';p.write_bytes(self.V.policy('gpu'));p.chmod(0o644)
        with self.assertRaises(ValueError):self.V.inspect(self.token)
        p.unlink();os.link(self.V.ROOT/self.token/'config/runtime.json',self.config.parent/'generated-hardlink')
        with self.assertRaises(ValueError):self.V.inspect(self.token)

    def test_existing_token_partial_or_complete_is_not_replayed(self):
        self.V.prepare(self.digest,self.token)
        before=(self.V.ROOT/self.token/'intent.json').read_bytes()
        with self.assertRaises(FileExistsError):self.V.prepare(self.digest,self.token)
        self.assertEqual((self.V.ROOT/self.token/'intent.json').read_bytes(),before)

    def test_original_config_change_during_preparation_refused(self):
        def change(event):
            if event=='configuration-copied':self.config.write_bytes(b'{"generated":"changed fixture"}\n')
        with self.assertRaises(ValueError):self.V.prepare(self.digest,self.token,checkpoint=change)
        self.assertFalse((self.V.ROOT/self.token/'complete.json').exists())

    def test_invalid_mode_token_and_unbound_configuration_refused_before_write(self):
        for token,mode in [('../bad','gpu'),('b'*32,'other'),('b'*31,'cpu')]:
            with self.assertRaises(ValueError):self.V.prepare(self.digest,token,mode)
        self.component['baseBinding']['configSha256']='0'*64
        with self.assertRaises(ValueError):self.V.prepare(self.digest,self.token)
        self.assertFalse(self.V.ROOT.exists())

    def test_four_process_death_boundaries_preserve_partial_or_complete_receipt(self):
        for index,event in enumerate(('intent-written','configuration-copied','before-complete','complete-written')):
            token=str(index+3)*32
            pid=os.fork()
            if pid==0:
                def stop(name):
                    if name==event:os._exit(73)
                try:self.V.prepare(self.digest,token,'gpu',checkpoint=stop)
                except BaseException:os._exit(91)
                os._exit(92)
            _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),73)
            if event=='complete-written':
                self.assertIs(self.V.inspect(token)['readyForActivation'],False)
            else:
                with self.assertRaises(ValueError):self.V.inspect(token)
            with self.assertRaises(FileExistsError):self.V.prepare(self.digest,token,'gpu')


if __name__=='__main__':unittest.main(verbosity=2)
