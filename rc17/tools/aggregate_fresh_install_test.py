# SPDX-License-Identifier: GPL-3.0-only
"""Fresh-install models only: no installer, service, network or VM execution."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os,sys,subprocess,tempfile
from pathlib import Path
import tarfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fresh_install', Path(__file__).with_name('aggregate-fresh-install.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class Model:
    def __init__(self, binding, user, lan):
        self.binding, self.user, self.lan = binding, user, lan
        self.root = Path('/var/lib/droidmate-fresh-install')
        self.package = self.root/'foundation/droiddeck-source-candidate'
        self.updater = self.root/'inputs/assets/core.pyz'
        self.events = []
        self.exists = False
        self.failure = None
        self.accept = []
        self.lose = None

    def preflight(self):
        self.events.append('preflight')
        if self.exists:
            raise ValueError('Fresh target required')

    @contextlib.contextmanager
    def claim(self, stager, assets, files):
        if self.exists:
            raise ValueError('Never replay')
        self.exists = True
        self.events.append('claim')
        try:
            yield self
        finally:
            self.events.append('closed')

    def verify(self):
        self.binding.check()

    def record(self, name, value):
        self.events.append(name)

    def run(self, label, args, timeout):
        self.events.append((label, args))
        if self.lose == label:
            self.binding.ctx['release']['packageSha256'] = 'b'*64
        if self.failure == label:
            raise RuntimeError('Synthetic uncertain child outcome')
        if label == 'foundation':
            return {'kind': 'clean-machine-installation', 'phasesCompleted': 35,
                    'transaction': '1'*12, 'servicesActivated': True,
                    'browserTrustConfigured': False, 'releaseAccepted': False}
        return {'kind': 'gateway-chain-installed', 'digest': F.CORE,
                'target': {'version': '0.1.1-rc.20'}, 'androidDataPreserved': True}

    def foundation_completed(self, result):
        self.events.append('foundation-proof')

    def policy_flags(self):
        self.events.append('policy-inspection')
        return self.accept

    def core_completed(self, result):
        self.events.append('core-proof')


class FreshInstallTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {'root': Path('/usr/local/share/droidmate-rc17-'+'a'*64),
                    'release': {'kind': 'droidmate-aggregate-release-authorization',
                                'aggregateRelease': 'RC17', 'coreRuntimeVersion': '0.1.1-rc.20',
                                'compatibleReleaseSha256': F.CORE, 'activationAllowed': True,
                                'packageSha256': 'a'*64}, 'check': lambda: True}
        self.assets = {n: ('asset '+n).encode() for n in F.ASSETS}
        self.pins = {n: {**r, 'size': len(self.assets[n]),
                         'sha256': hashlib.sha256(self.assets[n]).hexdigest()} for n,r in F.ASSETS.items()}
        self.enterContext(patch.object(F, 'ASSETS', self.pins))
        self.enterContext(patch.object(F, 'foundation_files', return_value={'fixture': (b'x', 0o644)}))
        self.loader = self.enterContext(patch.object(F, 'load_stager', return_value=object()))
        self.models = []
        def make(binding, user, lan):
            m = Model(binding, user, lan)
            self.models.append(m)
            return m
        self.enterContext(patch.object(F, 'Native', side_effect=make))

    def install(self):
        return F.fresh_install(self.ctx, self.assets, 'tester', '192.168.1.7')

    def test_original_commands_package_root_and_completion_boundary(self):
        result = self.install()
        m = self.models[0]
        commands = [v for v in m.events if isinstance(v, tuple)]
        self.assertEqual(commands, [
            ('foundation', ['/usr/bin/python3','-I','-B',str(self.ctx['root']/'source/tools/aggregate-build-toolchain.py'),
                            'foundation','tester','192.168.1.7']),
            ('core', ['/usr/bin/python3','-I','-B',str(m.updater), 'install','completed',str(m.package)])])
        self.assertTrue(result['readyForCaptureEnrollment'])
        self.assertFalse(result['releaseComplete'])
        self.assertEqual(result['rc2Package'], str(m.package))
        self.assertLess(m.events.index('foundation-proof'), m.events.index('policy-inspection'))
        self.assertLess(m.events.index('core-proof'), m.events.index('complete.json'))

    def test_every_asset_is_pinned_before_helper_or_filesystem_effect(self):
        for name in self.assets:
            with self.subTest(role=name):
                good = self.assets[name]
                self.assets[name] += b'tampered'
                with self.assertRaises(ValueError):
                    self.install()
                self.assets[name] = good
        self.loader.assert_not_called()
        self.assertEqual(self.models, [])

    def test_closed_assets_context_and_explicit_user_private_ipv4(self):
        for user,lan in [('root','192.168.1.7'), ('tester;id','192.168.1.7'),
                         ('tester','8.8.8.8'), ('tester','192.168.1.0'), ('tester','::1')]:
            with self.subTest(user=user,lan=lan), self.assertRaises(ValueError):
                F.fresh_install(self.ctx,self.assets,user,lan)
        self.ctx['release']['activationAllowed'] = False
        with self.assertRaises(ValueError): self.install()
        self.loader.assert_not_called()

    def test_failure_or_context_loss_never_dispatches_next_phase_or_completes(self):
        for failure in ('foundation','core'):
            with self.subTest(failure=failure), patch.object(F, 'Native',
                    side_effect=lambda b,u,l: self.failing_model(b,u,l,failure)):
                with self.assertRaises(RuntimeError): self.install()
                m = self.models[-1]
                self.assertNotIn('complete.json', m.events)
                self.assertTrue(m.exists)
                if failure == 'foundation':
                    self.assertFalse(any(isinstance(e,tuple) and e[0]=='core' for e in m.events))
        with patch.object(F, 'Native', side_effect=lambda b,u,l:self.failing_model(b,u,l,None,lose='core')):
            with self.assertRaises(ValueError): self.install()
            self.assertNotIn('complete.json', self.models[-1].events)

    def failing_model(self,b,u,l,failure,lose=None):
        m=Model(b,u,l);m.failure=failure;m.lose=lose;self.models.append(m);return m

    def test_existing_or_partial_claim_is_never_resumed(self):
        m=Model(F.Binding(self.ctx),'tester','192.168.1.7');m.exists=True
        with patch.object(F,'Native',return_value=m), self.assertRaises(ValueError): self.install()
        self.assertFalse(any(isinstance(e,tuple) for e in m.events))

    def test_mutable_callback_cannot_retarget_authenticated_context(self):
        def changed():
            self.ctx['root']=Path('/usr/local/share/foreign')
            return True
        self.ctx['check']=changed
        with self.assertRaises(ValueError): self.install()
        self.loader.assert_not_called()

    def test_updater_nonterminal_or_wrong_release_result_is_not_completion(self):
        original=Model.run
        for bad in [{'kind':'gateway-chain-already-installed','digest':F.CORE},
                    {'kind':'gateway-chain-installed','digest':'b'*64,'target':{'version':'0.1.1-rc.20'}},
                    {'kind':'gateway-chain-installed','digest':F.CORE,'target':{'version':'0.1.1-rc.19'}}]:
            def run(m,label,args,timeout):
                return bad if label=='core' else original(m,label,args,timeout)
            with patch.object(Model,'run',run),self.assertRaises(ValueError): self.install()
            self.assertNotIn('complete.json',self.models[-1].events)


    def test_explicit_existing_policy_flag_is_forwarded_only_after_admission(self):
        def make(b,u,l):
            m=Model(b,u,l);m.accept=['--accept-existing-policy'];self.models.append(m);return m
        with patch.object(F,'Native',side_effect=make):
            self.install()
        m=self.models[-1]
        command=next(args for label,args in (e for e in m.events if isinstance(e,tuple)) if label=='core')
        self.assertEqual(command[-1], '--accept-existing-policy')
        self.assertLess(m.events.index('policy-inspection'), m.events.index('core.started.json'))

    def test_missing_or_extra_asset_roles_never_dispatch_any_installer(self):
        for values in [{n:b for n,b in self.assets.items() if n!='coreSource'}, {**self.assets,'extra':b'x'}]:
            with self.assertRaises(ValueError):F.fresh_install(self.ctx,values,'tester','192.168.1.7')
        self.assertFalse(self.models)

    def test_lost_admission_after_completion_write_cannot_return_success(self):
        original=Model.record
        def record(m,name,value):
            original(m,name,value)
            if name=='complete.json':
                m.binding.ctx['release']['activationAllowed']=False
        with patch.object(Model,'record',record), self.assertRaises(ValueError):self.install()

    def test_frozen_stager_hash_is_checked_before_its_code_executes(self):
        raw=b'raise AssertionError("Unauthenticated helper executed")\n'
        # load_stager is patched only in this orchestration class; exercise the
        # production definition from a separate, inert module load.
        spec=importlib.util.spec_from_file_location('fresh_loader_test',Path(F.__file__))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module,'read_protected',return_value=(raw,())), self.assertRaisesRegex(ValueError,'frozen'):
            module.load_stager(module.Binding(self.ctx))


class NativeGuardTests(unittest.TestCase):
    def binding(self):
        return types.SimpleNamespace(check=lambda:True)

    def test_verify_never_calls_creation_capable_materializer_for_missing_inputs(self):
        n=F.Native(self.binding(),'tester','192.168.1.7')
        n.input_files={'asset':b'original'}
        calls=[]
        n.stager=types.SimpleNamespace(materialize=lambda *args:calls.append(args))
        with self.assertRaises(ValueError):n.verify()
        self.assertEqual(calls,[])

    def test_policy_dependency_loader_executes_retained_bytes_not_reopened_path(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='fresh-code-') as tmp:
            root=Path(tmp);entry=root/'entry.py'
            expected=b'value=42\n';entry.write_bytes(b"raise AssertionError('unretained path executed')\n")
            n=types.SimpleNamespace(verify=lambda:True,source=lambda path:expected)
            with F.verified_imports(n,root,{'entry.py':expected}):
                spec=importlib.util.spec_from_file_location('closed_policy_fixture',entry)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                self.assertEqual(module.value,42)
                with self.assertRaises(ValueError):importlib.util.spec_from_file_location('foreign',root/'outside.py')

    @unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'Actual isolated Linux child session required')
    def test_real_timeout_drains_descendant_before_return(self):
        with tempfile.TemporaryDirectory(prefix='aggregate-owned-descendant-') as tmp:
            marker=Path(tmp)/'owned-child.pid'
            code="import pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)',sys.argv[1]]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            def active(pid):
                p=Path('/proc')/str(pid)/'stat'
                try:return p.read_text().split(')')[-1].split()[0] not in ('Z','X')
                except FileNotFoundError:return False
            try:
                with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as error:
                    with self.assertRaises(subprocess.TimeoutExpired):F.run_owned([sys.executable,'-I','-B','-c',code,str(marker)],output,error,1)
                pid=int(marker.read_text());self.assertFalse(active(pid),'Owned descendant survived timeout return')
            finally:
                if marker.exists():
                    pid=int(marker.read_text());cmd=Path('/proc')/str(pid)/'cmdline'
                    if active(pid) and cmd.exists() and str(marker).encode() in cmd.read_bytes():os.kill(pid,9)

    def test_owned_process_supervisor_drains_group_before_timeout_returns(self):
        import subprocess
        events=[]
        class Process:
            pid=12345
            def wait(self,timeout):
                events.append('wait')
                if events.count('wait')==1:raise subprocess.TimeoutExpired(['owned'],timeout)
                return 0
        with patch.object(F.subprocess,'Popen',return_value=Process()) as start,patch.object(F.os,'killpg',side_effect=lambda pid,sig:events.append(('signal',pid,sig)),create=True),patch.object(F,'group_members',return_value=[]):
            with self.assertRaises(subprocess.TimeoutExpired):F.run_owned(['owned'],None,None,.01)
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        self.assertIn(('signal',12345,getattr(F.signal,'SIGKILL',9)),events)


class ParserTests(unittest.TestCase):
    def tar(self, rows):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:',format=tarfile.USTAR_FORMAT) as t:
            for name,raw,kind in rows:
                m=tarfile.TarInfo(name);m.mode=0o644;m.type=kind;m.size=len(raw) if kind==tarfile.REGTYPE else 0
                t.addfile(m,io.BytesIO(raw) if kind==tarfile.REGTYPE else None)
        return out.getvalue()

    def test_foundation_parser_rejects_foreign_traversal_links_and_duplicate_members(self):
        prefix='droiddeck-source-candidate/'
        for rows in [[('../outside',b'x',tarfile.REGTYPE)],
                     [(prefix+'x',b'x',tarfile.SYMTYPE)],
                     [(prefix+'x',b'x',tarfile.REGTYPE)]*2]:
            with self.subTest(rows=rows),self.assertRaises(ValueError):F.foundation_files(self.tar(rows))

    def test_policy_acceptance_is_exact_or_absent_and_never_a_retry_flag(self):
        self.assertEqual(F.policy_decision(None), [])
        self.assertEqual(F.policy_decision(F.POLICY_BYTES), ['--accept-existing-policy'])
        with self.assertRaises(ValueError):F.policy_decision(F.POLICY_BYTES+b'changed')


if __name__=='__main__':
    unittest.main()
