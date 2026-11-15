# SPDX-License-Identifier: GPL-3.0-only
"""Generated manager/namespace seams exercise real durable files, not live video."""
from contextlib import ExitStack,contextmanager
import copy
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

def load(name):
    s=importlib.util.spec_from_file_location('view_controller_test_'+name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
C=load('capture-view-controller');N=load('capture-video-admission');R=load('capture-video-recycle')
F=load('capture_video_admission_test');RF=load('capture_video_recycle_test')
NATIVE=sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0


class Pure(unittest.TestCase):
    def test_controller_admits_only_closed_operation_identifiers(self):
        for value in ('../other','--all','a'*31,'A'*32,'a'*32+'\n',None):
            with self.assertRaises(ValueError):C.token(value)
        self.assertEqual(C.token('a'*32),'a'*32)
    def test_record_does_not_adopt_partial_or_unknown_durable_state(self):
        for value in ({},{'schema':True},{'schema':1,'kind':'foreign'}):
            with self.assertRaises(ValueError):C.record(value)
    @unittest.skipUnless(shutil.which('node'),'Node packet-burst regression required')
    def test_media_probe_emits_one_result_when_queued_packets_arrive_after_terminate(self):
        harness=r"""
const vm=require('vm'),EventEmitter=require('events');
class W extends EventEmitter {
 constructor() { super(); queueMicrotask(()=>{
  for (const flag of [1n<<62n,1n<<61n,0n,0n,0n]) {
   const packet=Buffer.alloc(13);packet.writeBigUInt64BE(flag);packet.writeUInt32BE(1,8);
   this.emit('message',packet,true);
  }
 }); }
 close() {}
 terminate() {}
}
vm.runInNewContext(process.argv[1],{require:()=>W,process,Buffer,setTimeout,clearTimeout});
"""
        run=subprocess.run([shutil.which('node'),'-e',harness,C.MEDIA_JS],capture_output=True,text=True,timeout=5)
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout)
        self.assertEqual(result,{'configuration':True,'keyFrame':True,'frames':1})
    def test_initial_gpu_view_requires_actual_hardware_encoder_after_its_keyframe(self):
        e=C.Engine.__new__(C.Engine);e.guard=lambda:True;e.snapshot=lambda current,mapped:True
        e.view={'report':{'mode':'gpu'}};e.n=SimpleNamespace(container=123);e.manual_cpu_ready=lambda:False
        current={'InvocationID':'a'*32}
        base=b'Compositor H264 configuration and first key frame ready\n'
        def run(log):
            return patch.object(C,'command',side_effect=lambda args,*rest:log if args[0]=='/usr/bin/journalctl' else b'{"configuration":true,"keyFrame":true,"frames":1}')
        with run(base):self.assertFalse(e.ready(current,mapped=True))
        for log in [base+b'Experimental VAAPI encoder unavailable; using CPU encoder\n',base+b'Experimental GPU resize active; software H264 retained\n']:
            with run(log):
                with self.assertRaises(ValueError):e.ready(current,mapped=True)
        with run(base+b'Experimental VAAPI H264 encoder active\n'):
            self.assertTrue(e.ready(current,mapped=True))
        with run(base):self.assertTrue(e.ready(current,mapped=False))

    def test_manual_cpu_admission_requires_current_root_report_and_service_generation(self):
        e=C.Engine.__new__(C.Engine);proc={'pid':123,'birth':456};boot='a'*36
        e.n=SimpleNamespace(video_process=lambda:dict(proc),boot=boot)
        e.view={'runtimeRoot':'/held/signed/runtime'}
        good={'preference':'cpu','effective':'cpu','gpuAvailable':True,'pid':123,'birth':'456','boot':boot}
        with patch.object(C,'command',return_value=json.dumps(good).encode()) as run:
            self.assertTrue(e.manual_cpu_ready())
            self.assertEqual(run.call_args.args[0][-1],str(Path('/held/signed/runtime')/'preview-gateway/video-encoding.js'))
        for key,value in [('preference','auto'),('effective','starting'),('gpuAvailable',False),('pid',124),('birth','455'),('boot','b'*36)]:
            with patch.object(C,'command',return_value=json.dumps({**good,key:value}).encode()):
                self.assertFalse(e.manual_cpu_ready(),key)
        with patch.object(C,'command',side_effect=ValueError('untrusted status')):self.assertFalse(e.manual_cpu_ready())
        e.n.video_process=lambda:dict(proc)
        def changed(*args):
            e.n.video_process=lambda:{'pid':124,'birth':457}
            return json.dumps(good).encode()
        with patch.object(C,'command',side_effect=changed):self.assertFalse(e.manual_cpu_ready())

    def test_manual_cpu_still_requires_keyframe_media_and_unchanged_selection(self):
        e=C.Engine.__new__(C.Engine);e.guard=lambda:True;e.snapshot=lambda current,mapped:True
        e.view={'report':{'mode':'gpu'}};e.n=SimpleNamespace(container=123)
        current={'InvocationID':'a'*32};base=b'Compositor H264 configuration and first key frame ready\n'
        def run(log,media):
            return patch.object(C,'command',side_effect=lambda args,*rest:log if args[0]=='/usr/bin/journalctl' else json.dumps(media).encode())
        with patch.object(e,'manual_cpu_ready',return_value=True):
            with run(base,{'configuration':True,'keyFrame':True}):self.assertTrue(e.ready(current,mapped=True))
            with run(b'',{'configuration':True,'keyFrame':True}):self.assertFalse(e.ready(current,mapped=True))
            with run(base,{'configuration':True,'keyFrame':False}):self.assertFalse(e.ready(current,mapped=True))
        with patch.object(e,'manual_cpu_ready',side_effect=[True,False]):
            with run(base,{'configuration':True,'keyFrame':True}):self.assertFalse(e.ready(current,mapped=True))

    def test_recovery_template_has_no_expiry_or_unrelated_worker_actions(self):
        text=C.RECOVER_TEXT.decode()
        self.assertIn('ExecStart=/usr/bin/python3 -I -B /var/lib/droiddeck-capture-loader/bootstrap.py recover %i',text)
        self.assertNotIn(chr(92),text)
        after=next(l.split('=',1)[1].split() for l in text.splitlines() if l.startswith('After='))
        self.assertIn('droiddeck-update-boot.service',after)
        self.assertIn('tesla-waydroid-runtime.service',after)
        for forbidden in ('RuntimeMaxSec','waydroid-container','ExecStop=','rm ','Restart=always'):
            self.assertNotIn(forbidden,text)


@unittest.skipUnless(NATIVE,'Root Linux generated durable operation/fork tests required')
class Durable(unittest.TestCase):
    def setUp(self):
        self.S=load('capture-component-stage')
        self.temp=tempfile.TemporaryDirectory(prefix='vc-',dir='/run');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.drops=self.root/'drops';self.drops.mkdir(mode=0o700)
        self.drop=self.drops/(N.VIDEO+'.d')/'90-droiddeck-capture-view.conf'
        self.patches=[patch.object(C,'ROOT',self.root/'operation'),patch.object(C,'SOCKET',self.root/'operation/control.sock'),patch.object(N,'DROP',str(self.drop)),patch.object(R,'OWNED_DROP',str(self.drop))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.token='b'*32;self.component='a'*64;self.package='c'*64
        service,unit=F.properties();self.base=N.project(service,unit)
        self.view={'runtimeRoot':'/var/lib/droiddeck-capture/'+self.component+'/runtime',
            'configRoot':'/var/lib/droiddeck-capture-views/'+self.token+'/config',
            'mapping':self.S.P.encode({'generated':'mapping only; never installed on real service'}),
            'report':{'kind':'owned-capture-private-view','token':self.token,'componentSha256':self.component,'mode':'gpu','activationAllowed':False},
            'check':lambda:True}
        self.control={'statement':{'packageSha256':self.package,'componentSha256':self.component},'check':lambda:True}
        self.state=self.root/'fake-manager.json'
    def backend(self):
        outer=self
        class Backend(RF.Fake):
            def __init__(self):
                super().__init__();self.loaded=False;self.namespace=False;self.next_namespace=False
                if outer.state.exists():
                    for k,v in json.loads(outer.state.read_bytes()).items():setattr(self,k,v)
                self.persist()
            def persist(self):
                outer.state.write_text(json.dumps({k:getattr(self,k) for k in ('video','new','clock','signals','sent','loaded','namespace','next_namespace')}))
                outer.state.chmod(0o600)
            def handle(self,value):
                # A new pidfd belongs to the current live generation, not the
                # already-exited generation represented by the previous handle.
                self.sent=False;self.persist()
                return super().handle(value)
            def terminate(self,fd):
                pid=int(self.video['MainPID']);count=int(self.video['NRestarts']);start=int(self.video['ExecMainStartTimestampMonotonic'])
                self.new=RF.unit(self.video_unit,pid+1,count+1,format(pid+1,'032x'),start+1000)
                self.new['DropInPaths']=str(outer.drop) if self.loaded else ''
                self.next_namespace=self.loaded
                super().terminate(fd);self.persist()
            def sleep(self):
                self.clock+=.1
                if self.video['ActiveState']=='activating':
                    self.video=copy.deepcopy(self.new);self.namespace=self.next_namespace
                self.persist()
            def reload(self):
                self.loaded=outer.drop.exists();self.video['DropInPaths']=str(outer.drop) if self.loaded else '';self.persist()
        return Backend()
    @contextmanager
    def engine(self,checkpoint=lambda event:None):
        b=self.backend();outer=self
        class Bus:
            def video(inner):
                model=copy.deepcopy(outer.base)
                if b.loaded:model.update(PrivateMounts=True,BindReadOnlyPaths=N.view_mounts(outer.view['runtimeRoot'],outer.view['configRoot']),DropInPaths=[str(outer.drop)])
                return dict(b.video),model,{}
        native=SimpleNamespace(boot=b.boot_id+'\n',peers=b.peers,bus=Bus(),check=lambda:True,
            video_process=lambda:{'pid':int(b.video['MainPID']),'birth':int(b.video['MainPID'])*100})
        def command(args,timeout=15):
            if args!=['/usr/bin/systemctl','daemon-reload']:raise AssertionError('No real system command allowed in generated seam')
            b.reload();return b''
        with ExitStack() as stack,patch.object(C,'command',command):
            store=C.Store(stack,self.S,create=True)
            e=C.Engine(self.control,SimpleNamespace(S=self.S),N,R,self.view,native,store,checkpoint=checkpoint)
            e.b=b;e.original_files=lambda proc:{n:'d'*64 for n in C.FILES}
            e.ready=lambda current,mapped: b.namespace==mapped
            yield e,b
    def assert_original(self,e,b):
        self.assertEqual(e.store.value()['phase'],'restored')
        self.assertFalse(self.drop.exists());self.assertFalse(b.namespace);self.assertFalse(b.loaded)
        self.assertEqual(b.peers,RF.Fake().peers)
    def test_real_files_publish_before_signal_and_restore_worker_before_terminal_receipt(self):
        events=[]
        with self.engine(events.append) as (e,b):
            result=e.change();self.assertEqual(result['phase'],'accepted');self.assertTrue(b.namespace)
            self.assertEqual(e.store.value()['phase'],'accepted');self.assertEqual(len(b.signals),1)
            result=e.restore();self.assertEqual(result['phase'],'restored');self.assertEqual(len(b.signals),2)
            self.assert_original(e,b)
        self.assertLess(events.index('durable-prepared'),events.index('mapping-published'))
        self.assertLess(events.index('mapping-removed-worker-pending'),events.index('durable-restored'))
    def test_foreign_dropin_and_same_byte_inode_replacement_are_not_deleted(self):
        with self.engine() as (e,b):
            e.change();raw=self.drop.read_bytes();original=self.drop.with_suffix('.foreign-original')
            self.drop.rename(original);self.drop.write_bytes(raw);self.drop.chmod(0o600)
            with self.assertRaises(ValueError):e.restore()
            self.assertTrue(self.drop.exists());self.assertTrue(original.exists());self.assertTrue(b.namespace)
    def test_native_node_change_between_check_and_cycle_cannot_receive_signal(self):
        with self.engine() as (e,b):
            e.change();before=len(b.signals)
            original=e.n.video_process
            def race():
                checked=original();b.video.update(MainPID='99',InvocationID='f'*32);b.persist();return checked
            e.n.video_process=race
            with self.assertRaisesRegex(ValueError,'Node generation changed'):
                e.ensure(True,force=True)
            self.assertEqual(len(b.signals),before)
    def test_operation_file_foreign_replacement_prevents_signal(self):
        with self.engine() as (e,b):
            e.change();file=C.ROOT/'state.json';raw=file.read_bytes();file.rename(C.ROOT/'foreign.json');file.write_bytes(raw);file.chmod(0o600)
            before=len(b.signals)
            with self.assertRaises(ValueError):e.restore()
            self.assertEqual(len(b.signals),before);self.assertTrue(self.drop.exists())
    def test_restored_operation_can_be_archived_for_another_trial(self):
        with self.engine() as (e,b):
            e.change();first=e.op['token'];e.restore()
        with self.engine() as (e,b):
            e.change();self.assertNotEqual(first,e.op['token']);e.restore();self.assert_original(e,b)
            self.assertTrue((C.ROOT/('history-'+first+'.json')).exists())
    def test_crossboot_rebind_uses_stable_environment_and_fresh_guards(self):
        with self.engine() as (e,b):
            e.change();e.op['boot']='00000000-0000-0000-0000-000000000001';e.op['peers']={};e.store.save(e.op)
            with self.assertRaises(ValueError):e.guard()
            e.rebind();self.assertEqual(e.op['peers'],e.n.peers);e.restore();self.assert_original(e,b)
    def test_crossboot_rebind_cannot_hide_unrelated_environment_changes(self):
        with self.engine() as (e,b):
            e.change();before=len(b.signals);e.op['baseline']['StableEnvironmentSha256']='0'*64;e.store.save(e.op)
            with self.assertRaises(ValueError):e.rebind()
            self.assertEqual(len(b.signals),before)
    def test_new_trial_cleans_prior_owned_socket_before_archiving_restored_state(self):
        pid=os.fork()
        if pid==0:
            def death(event):
                if event=='durable-restored':os._exit(77)
            try:
                with self.engine(death) as (e,b):
                    e.change()
                    with patch.object(C,'notify_ready',side_effect=lambda:e.restore()):C.serve(e)
            except Exception:os._exit(91)
            os._exit(92)
        _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),77)
        self.assertTrue(C.SOCKET.exists())
        with self.engine() as (e,b):
            old=e.op['token'];self.assertEqual(e.op['phase'],'restored')
            e.change();self.assertFalse(C.SOCKET.exists());self.assertNotEqual(e.op['token'],old)
            e.restore();self.assert_original(e,b)
    def test_actual_socket_publication_deaths_never_publish_unknown_endpoint(self):
        for point in ('socket-created-before-receipt','socket-published'):
            case=self.root/point;case.mkdir(mode=0o700);(case/'drops').mkdir(mode=0o700)
            old_state,old_drop=self.state,self.drop;self.state=case/'fake-manager.json';self.drop=case/'drops'/(N.VIDEO+'.d')/'90-droiddeck-capture-view.conf'
            with patch.object(C,'ROOT',case/'operation'),patch.object(C,'SOCKET',case/'operation/control.sock'),patch.object(N,'DROP',str(self.drop)),patch.object(R,'OWNED_DROP',str(self.drop)):
                pid=os.fork()
                if pid==0:
                    def death(event):
                        if event==point:os._exit(77)
                    try:
                        with self.engine(death) as (e,b):
                            e.change()
                            with patch.object(C,'notify_ready',return_value=None):C.serve(e)
                    except Exception:os._exit(91)
                    os._exit(92)
                _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),77,point)
                with self.engine() as (e,b):
                    e.restore();C.cleanup_socket(e);self.assert_original(e,b)
                    self.assertFalse(C.SOCKET.exists())
            self.state,self.drop=old_state,old_drop
    def test_four_actual_operator_deaths_recover_under_new_file_lock(self):
        for point in ('intent-durable','directory-created-before-receipt','mapping-published','durable-accepted','mapping-removed-worker-pending','directory-removed-before-restored'):
            # Separate case subroot; no case reuses a consumed operation namespace.
            case=self.root/point;case.mkdir(mode=0o700);(case/'drops').mkdir(mode=0o700)
            old_state,old_drop=self.state,self.drop;self.state=case/'fake-manager.json';self.drop=case/'drops'/(N.VIDEO+'.d')/'90-droiddeck-capture-view.conf'
            with patch.object(C,'ROOT',case/'operation'),patch.object(C,'SOCKET',case/'operation/control.sock'),patch.object(N,'DROP',str(self.drop)),patch.object(R,'OWNED_DROP',str(self.drop)):
                pid=os.fork()
                if pid==0:
                    def death(event):
                        if event==point:os._exit(77)
                    try:
                        with self.engine(death) as (e,b):
                            e.change()
                            if point in ('mapping-removed-worker-pending','directory-removed-before-restored'):e.restore()
                    except Exception:os._exit(91)
                    os._exit(92)
                _,status=os.waitpid(pid,0)
                self.assertEqual(os.waitstatus_to_exitcode(status),77,point)
                with self.engine() as (e,b):
                    e.restore();self.assert_original(e,b)
            self.state,self.drop=old_state,old_drop


if __name__=='__main__':unittest.main(verbosity=2)
