# SPDX-License-Identifier: GPL-3.0-only
import importlib.util,types,unittest
from pathlib import Path
from unittest import mock
spec=importlib.util.spec_from_file_location('aggregate_supervisor',Path(__file__).with_name('aggregate-supervisor.py'))
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)

def ctx():return {'root':Path('/usr/local/share/droidmate-rc18-'+'a'*64),'release':{'packageSha256':'a'*64},'check':lambda:True}

class SupervisorTests(unittest.TestCase):
 def test_installer_is_oneshot_owned_recovery_on_failure_and_no_production_expiry(self):
  text=S.installer_unit(ctx()).decode()
  self.assertIn('Type=oneshot',text);self.assertIn('OnFailure=droidmate-aggregate-recovery.service',text)
  self.assertIn('OnFailureJobMode=fail',text);self.assertIn('KillMode=control-group',text)
  self.assertIn('--enrolled install-existing',text);self.assertNotIn('RuntimeMaxSec',text)
  self.assertIn('TimeoutStartSec=1800',text);self.assertNotIn('Restart=always',text)
 def test_current_worker_requires_kernel_cgroup_actual_argv_and_pid1_namespace(self):
  c=ctx();expected=S.worker_argv(c)
  fields={'FragmentPath':S.INSTALLER_PATH,'DropInPaths':'','MainPID':'1234','Type':'oneshot','KillMode':'control-group','OnFailure':S.RECOVERY,'OnFailureJobMode':'fail'}
  self.assertTrue(S.validate_worker(c,1234,fields,S.CGROUP,expected,True))
  for kwargs in [{'pid':2345},{'group':b'0::/user.slice/other\n'},{'argv':expected+[b'foreign']},{'same_namespace':False}, {'fields':{**fields,'DropInPaths':'/foreign'}}]:
   values={'pid':1234,'fields':fields,'group':S.CGROUP,'argv':expected,'same_namespace':True,**kwargs}
   with self.assertRaises(ValueError):S.validate_worker(c,**values)
 def test_foreign_control_unit_is_never_started(self):
  events=[]
  class Mapping:
   def __init__(self,key):self.key=key
   def prepare(self):
    events.append(self.key)
    if self.key=='recovery':raise ValueError('Foreign recovery unit')
   def publish(self):events.append('publish')
   def verify(self):return True
  engine=types.SimpleNamespace(OwnedMapping=lambda j,k,b:Mapping(k),OwnedBootLink=lambda j:Mapping('boot-link'))
  s=S.Supervisor(ctx(),engine,types.SimpleNamespace(guard=lambda:True))
  with self.assertRaises(ValueError):s.prepare(b'recovery')
  self.assertEqual(events,['recovery'])
 def test_start_rechecks_controls_and_does_not_stop_worker_on_caller_failure(self):
  events=[];s=object.__new__(S.Supervisor);s.context=lambda:True
  s.controls=types.SimpleNamespace(check=lambda:events.append('check') or True);s.graph=lambda:True;s.recovery_graph=lambda:True
  def command(args,timeout):events.append(tuple(args));raise TimeoutError('caller disconnected')
  s.command=command
  with self.assertRaises(TimeoutError):s.start()
  self.assertIn(('/usr/bin/systemctl','start',S.INSTALLER),events)
  self.assertFalse(any('stop' in e for e in events if type(e) is tuple))
 def test_foreign_effective_recovery_override_refuses_before_start(self):
  s=object.__new__(S.Supervisor);s.context=lambda:True;s.controls=types.SimpleNamespace(check=lambda:True)
  s.expected_recovery=b'[Unit]\nDescription=test\n[Service]\nType=oneshot\nExecStart=/usr/bin/true\n'
  s.c=types.SimpleNamespace(RECOVERY_UNIT='/etc/systemd/system/'+S.RECOVERY)
  s.observe=lambda names,unit=None:{'FragmentPath':s.c.RECOVERY_UNIT,'DropInPaths':'/etc/foreign.conf','Type':'oneshot','ExecStart':'foreign'}
  calls=[];s.command=lambda args,timeout:calls.append(args)
  with self.assertRaises(ValueError):s.start()
  self.assertEqual(calls,[])

 def test_systemd_missing_unsupported_optional_property_is_not_silently_accepted(self):
  s=object.__new__(S.Supervisor);s.context=lambda:True
  s.command=lambda args,timeout:'FragmentPath=/etc/owned\n'
  with self.assertRaises(ValueError):s.observe(('FragmentPath','ExecStart'))

 def test_observation_requests_all_properties_so_empty_command_arrays_are_admitted(self):
  seen=[];s=object.__new__(S.Supervisor);s.context=lambda:True
  s.command=lambda args,timeout:seen.append(args) or 'ExecStartPre=\n'
  self.assertEqual(s.observe(('ExecStartPre',)),{'ExecStartPre':''})
  self.assertIn('--all',seen[0])

 def test_omitted_empty_exec_array_requires_structured_system_bus_proof(self):
  seen=[];s=object.__new__(S.Supervisor);s.context=lambda:True
  def command(args,timeout):
   seen.append(args)
   return '{"type":"a(sasbttttuii)","data":[]}' if args[0]=='/usr/bin/busctl' else 'FragmentPath=/etc/owned\n'
  s.command=command
  self.assertEqual(s.observe(('FragmentPath','ExecStartPre')),{'FragmentPath':'/etc/owned','ExecStartPre':''})
  self.assertEqual(seen[-1][0],'/usr/bin/busctl')
  s.command=lambda args,timeout:'{"type":"a(sasbttttuii)","data":[["/foreign"]]}' if args[0]=='/usr/bin/busctl' else 'FragmentPath=/etc/owned\n'
  with self.assertRaises(ValueError):s.observe(('FragmentPath','ExecStartPre'))

 def test_only_new_installs_can_launch_and_partial_install_cannot_replay(self):
  self.assertEqual(S.route_install('new',False),'launch')
  self.assertEqual(S.route_install('new',True),'worker')
  self.assertEqual(S.route_install('accepted',False),'terminal')
  for phase in ['intent','armed','cpu','stopped','mapped','recovering','restored']:
   for worker in (False,True):
    with self.assertRaises(ValueError):S.route_install(phase,worker)

 def test_root_context_cannot_retarget_between_checks(self):
  c=ctx();s=S.Supervisor(c,None,None);s.context();c['root']=Path('/foreign')
  with self.assertRaises(ValueError):s.context()

if __name__=='__main__':unittest.main()
