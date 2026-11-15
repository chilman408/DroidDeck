# SPDX-License-Identifier: GPL-3.0-only
"""Failure seams for the real handoff state machine; no VM/service operations."""
import importlib.util,unittest,os,sys,tempfile
from contextlib import ExitStack
from unittest import mock
from pathlib import Path
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
   e=H.SuccessorHandoff.__new__(H.SuccessorHandoff);e.guard_verify=lambda:True;e.check=lambda:True
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
