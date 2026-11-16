# SPDX-License-Identifier: GPL-3.0-only
"""Failure seams for the real handoff state machine; no VM/service operations."""
import importlib.util,unittest,os,sys,tempfile,stat,hashlib
from contextlib import ExitStack,contextmanager
from unittest import mock
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('handoff',Path(__file__).with_name('rc18-predecessor-handoff.py'));H=importlib.util.module_from_spec(spec);spec.loader.exec_module(H)
class Journal:
 def __init__(self):self.phase='new';self.events=[]
 def state(self):return self.phase
 def append(self,phase):self.events.append(phase);self.phase=phase
class Backend:
 def __init__(self,journal,fail=None,kill=False):self.j=journal;self.calls=[];self.fail=fail;self.kill=kill
 def __getattr__(self,name):
  def call():
   self.calls.append((name,self.j.phase))
   if name==self.fail:
    if self.kill:raise KeyboardInterrupt('simulated process death')
    raise ValueError('simulated refusal')
   return True
  return call
class Handoff(unittest.TestCase):
 def test_publisher_predecessor_guard_pins_are_exact_sha256_not_native_path_receipts(self):
  import re
  for value in H.HANDOFF_PREDECESSORS.values():
   if 'handoffRecoverySha256' in value:self.assertRegex(value['handoffRecoverySha256'],r'^[a-f0-9]{64}$')
  self.assertEqual(H.HANDOFF_PREDECESSORS['baf49445cc9e331d6ee9a3ba71eb48ad3e9723301ff60874f99723e3f73d0526']['handoffRecoverySha256'],'ed1f732e60c34603ca396cd6cedf8519129e657102514c4c66eed0032fc3f756')
 def test_current_direct_install_allows_absent_guard_but_preserves_foreign_links_and_overrides(self):
  h=H.SuccessorHandoff.__new__(H.SuccessorHandoff);h.predecessor=H.HANDOFF_PREDECESSORS['14703568a3cf09351bbfa8e1f435b5cec0cb936ea006eb6e8316a5c9d2ec141b'];h.run=lambda args:self.fail('Absent optional guard must not call systemctl')
  with mock.patch.object(H,'Path',PurePosixPath,create=True),mock.patch.object(H,'os',os,create=True):
   with mock.patch.object(os.path,'lexists',return_value=False):h.retire_previous_guard()
   for fragment in ('multi-user.target.wants','/run/systemd/system/', '.service.d'):
    with self.subTest(fragment=fragment),mock.patch.object(os.path,'lexists',side_effect=lambda p:fragment in str(p)):
     with self.assertRaises(ValueError):h.retire_previous_guard()
 def test_present_pinned_guard_drains_and_disables_without_resetting_unloaded_successful_unit(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);directory=root/'etc/systemd/system';directory.mkdir(parents=True);raw=b'exact completed guard';aggregate='a'*64;unit='droidmate-successor-recovery-'+aggregate[:16]+'.service';(directory/unit).write_bytes(raw)
   h=H.SuccessorHandoff.__new__(H.SuccessorHandoff);h.predecessor={'aggregate':aggregate,'handoffRecoverySha256':hashlib.sha256(raw).hexdigest()};calls=[]
   h.b=SimpleNamespace(Tree=lambda stack:SimpleNamespace(file=lambda *args:raw));h.run=lambda args,**kw:calls.append((args,kw))
   with mock.patch.object(H,'Path',lambda raw:root/str(raw).lstrip('/'),create=True),mock.patch.object(H,'os',os,create=True),mock.patch.object(H,'stat',stat,create=True),mock.patch.object(H,'sha',lambda raw:hashlib.sha256(raw).hexdigest(),create=True),mock.patch.object(H,'ExitStack',ExitStack,create=True):h.retire_previous_guard()
   self.assertEqual(calls,[(['/usr/bin/systemctl','stop',unit],{'timeout':200}),(['/usr/bin/systemctl','disable',unit],{})])
 def test_restored_cpu_retry_reenables_only_exact_quiet_recovery_before_frozen_retirement(self):
  for state in ('enabled','disabled','masked','foreign'):
   with self.subTest(state=state):
    h=H.SuccessorHandoff.__new__(H.SuccessorHandoff);h.old_capture='a'*64;old={};events=[];template=PurePosixPath('/etc/systemd/system/droiddeck-capture-recover@.service');drop=PurePosixPath('/etc/systemd/system/owned.conf')
    h.program=SimpleNamespace(RECOVERY_TEMPLATE=template,SERVICE=PurePosixPath('/etc/systemd/system/default.service'),handoff_path=lambda pin:drop,recovery_unit=lambda:b'template');h.program_raw=b'program'
    h.guard_verify=lambda:events.append('guard');h.verify_cpu=lambda **kw:events.append('cpu');h.check=lambda:events.append('check');h.run=lambda args:events.append(args)
    fields={'LoadState':'loaded','ActiveState':'inactive','Result':'success','FragmentPath':str(template),'DropInPaths':str(drop),'OnSuccess':h.program.SERVICE.name,'OnSuccessJobMode':'fail','UnitFileState':state}
    if state=='foreign':fields['FragmentPath']='/foreign'
    h.i=SimpleNamespace(system_fields=lambda *args:fields,quiet=lambda f:events.append('quiet'),payload=lambda *args:{'handoff':b'handoff'},recovery_graph=lambda *args:events.append('original-graph'))
    h.b=SimpleNamespace(Tree=lambda stack:SimpleNamespace(file=lambda path,*args:b'template' if path==template else b'handoff',check=lambda:True))
    with mock.patch.object(H,'Path',PurePosixPath,create=True),mock.patch.object(H,'os',os,create=True),mock.patch.object(H,'ExitStack',ExitStack,create=True),mock.patch.object(os.path,'lexists',return_value=False):
     if state in ('masked','foreign'):
      with self.assertRaises(ValueError):h.prepare_retirement_recovery(old)
      self.assertFalse(any(isinstance(x,list) for x in events))
     else:
      h.prepare_retirement_recovery(old)
      self.assertEqual([x for x in events if isinstance(x,list)],[] if state=='enabled' else [['/usr/bin/systemctl','enable','droiddeck-capture-recover@'+'a'*64+'.service']])
      self.assertEqual(events[-2:],['original-graph','check'])
 def test_staging_reuses_only_exact_completed_signed_controls_without_forward_replay(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);names={'package.tar':b'package','statement.json':b'statement','signature.bin':b'signature'}
   for exists,tamper,incomplete in ((False,False,False),(True,False,False),(True,True,False),(True,False,True)):
    with self.subTest(exists=exists,tamper=tamper,incomplete=incomplete):
     h=H.SuccessorHandoff.__new__(H.SuccessorHandoff);h.inputs=root/'inputs';calls=[]
     @contextmanager
     def hold_self():yield
     @contextmanager
     def hold_controls(pin):
      if incomplete:raise ValueError('Incomplete signed staging preserved')
      yield {'check':lambda:calls.append('checked')}
     def read(path,*args):return b'foreign' if tamper and path.name=='signature.bin' else names[path.name]
     h.b=SimpleNamespace(ROOT=root,hold_self=hold_self,hold_controls=hold_controls,stage=lambda *args:calls.append('stage'),Tree=lambda stack:SimpleNamespace(file=read,check=lambda:True))
     with mock.patch.object(H,'os',os,create=True),mock.patch.object(H,'ExitStack',ExitStack,create=True),mock.patch.object(os.path,'lexists',return_value=exists):
      if tamper or incomplete:
       with self.assertRaises(ValueError):h.stage_controls(names)
       self.assertNotIn('stage',calls)
      else:
       h.stage_controls(names);self.assertEqual(calls.count('stage'),0 if exists else 1);self.assertIn('checked',calls)
 def test_shared_runtime_selects_only_exact_pinned_installation_origin_and_saved_entry(self):
  capture='2c7d495a3ed6ed096ec1285a08389c04be8c9070f83a3dfb9b8a5c889aacb394'
  for value in H.HANDOFF_SHARED_PREDECESSORS[capture]:
   self.assertEqual(H.select_predecessor(capture,history='droidmate-gpu-install-rc18-'+value['aggregate'][:16]),value)
   self.assertEqual(H.select_predecessor(capture,entry=value['entry']),value)
   for key in ('aggregate','entry','handoffRecoverySha256'):self.assertRegex(value[key],r'^[a-f0-9]{64}$')
  for kw in ({},{'history':'foreign'},{'entry':'0'*64}):
   with self.assertRaises(ValueError):H.select_predecessor(capture,**kw)
  with self.assertRaises(ValueError):H.select_predecessor('0'*64,history='droidmate-gpu-install-rc17-c80a06892581d6f0')
 def test_success_commits_every_boundary_and_does_not_replay_accepted(self):
  j=Journal();b=Backend(j);self.assertEqual(H.handoff_transact(j,b,'upgrade-existing')['phase'],'accepted')
  self.assertEqual(j.events,list(H.HANDOFF_PHASES[1:]));self.assertEqual([n for n,_ in b.calls if n!='check'],list(H.HANDOFF_METHODS))
  b.calls=[];H.handoff_transact(j,b,'upgrade-existing');self.assertEqual([n for n,_ in b.calls],['check','verify','finish'])
 def test_all_refusals_and_process_deaths_go_to_cpu_without_forward_replay(self):
  for method in H.HANDOFF_METHODS:
   for kill in (False,True):
    with self.subTest(method=method,kill=kill):
     j=Journal();b=Backend(j,method,kill)
     with self.assertRaises((ValueError,KeyboardInterrupt)):H.handoff_transact(j,b,'upgrade-existing')
     self.assertNotEqual(j.phase,'new','intent must precede external side effects')
     with self.assertRaises(ValueError):H.handoff_transact(j,Backend(j),'upgrade-existing')
     recovery=Backend(j);H.handoff_transact(j,recovery,'upgrade-recover')
     self.assertEqual(j.phase,'restored');self.assertEqual([n for n,_ in recovery.calls if n!='check'],['recover_cpu','verify_cpu','finish'])
     again=Backend(j);H.handoff_transact(j,again,'upgrade-recover');self.assertNotIn('recover_cpu',[n for n,_ in again.calls])
 def test_failed_cpu_proof_preserves_recovery_and_never_commits_restored(self):
  j=Journal();j.append('mapped')
  with self.assertRaises(ValueError):H.handoff_transact(j,Backend(j,'verify_cpu'),'upgrade-recover')
  self.assertEqual(j.phase,'recovering');self.assertNotIn('restored',j.events)
 def test_recovery_never_adopts_foreign_healthy_claim_or_retries_install(self):
  for phase in H.HANDOFF_PHASES[1:-1]:
   j=Journal();j.append(phase);b=Backend(j,'recover_cpu')
   with self.assertRaises(ValueError):H.handoff_transact(j,b,'upgrade-recover')
   self.assertEqual(j.phase,'recovering');self.assertNotIn('restored',j.events)
   names=[n for n,_ in b.calls];self.assertNotIn('install_successor',names);self.assertNotIn('retire_preference',names)
 def test_accepted_recovery_verifies_health_without_reinstalling(self):
  j=Journal();j.append('accepted');b=Backend(j);H.handoff_transact(j,b,'upgrade-recover')
  self.assertEqual([n for n,_ in b.calls],['check','verify','finish']);self.assertEqual(j.events,['accepted'])
 def test_new_recovery_does_nothing_and_foreign_actions_are_refused(self):
  j=Journal();b=Backend(j);H.handoff_transact(j,b,'upgrade-recover');self.assertEqual(b.calls,[('check','new')])
  for action in ('install','retire','--force',None):
   with self.assertRaises(ValueError):H.handoff_transact(j,b,action)
class NativeInterfaces(unittest.TestCase):
 @unittest.skipUnless(sys.platform=='linux','Native flock retirement regression')
 def test_planning_locks_close_before_retirement_and_exited_helper_is_stopped(self):
  import fcntl
  with tempfile.TemporaryDirectory() as tmp:
   e=H.SuccessorHandoff.__new__(H.SuccessorHandoff);e.guard_verify=lambda:True;e.check=lambda:True;e.prepare_retirement_recovery=lambda old:True
   e.j=SimpleNamespace(read=lambda name:{'preference':{'packageSha256':'a'*64}})
   e.program=SimpleNamespace(Native=type('Native',(),{}),SERVICE=Path('/etc/systemd/system/droidmate-gpu-default.service'))
   e.program_raw=b'known program';e.history=Path(tmp)/'history';unit=b'known unit';active=[True];calls=[]
   e.b=SimpleNamespace(Tree=lambda stack:SimpleNamespace(file=lambda *args:unit))
   def run(args):
    self.assertEqual(args,['/usr/bin/systemctl','stop',e.program.SERVICE.name]);active[0]=False
   e.run=run
   def transaction(b,tree,program,preference,history,action):
    self.assertFalse(active[0],'active/exited helper must be quiesced first')
    fd=os.open(Path(tmp)/'lock',os.O_RDWR|os.O_CREAT,0o600);tree.stack.callback(os.close,fd)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);calls.append(action)
   e.i=SimpleNamespace(payload=lambda *args:{'unit':unit},system_fields=lambda *args:{'FragmentPath':str(e.program.SERVICE),'DropInPaths':'','ActiveState':'active' if active[0] else 'inactive'},transaction=transaction)
   with mock.patch.object(H,'ExitStack',ExitStack,create=True):e.retire_preference()
   self.assertEqual(calls,['plan-retire','retire'])
if __name__=='__main__':unittest.main()
