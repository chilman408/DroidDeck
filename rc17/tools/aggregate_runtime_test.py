# SPDX-License-Identifier: GPL-3.0-only
import importlib.util,types,unittest
from pathlib import Path
from unittest import mock
spec=importlib.util.spec_from_file_location('runtime_integration',Path(__file__).with_name('aggregate-runtime.py'))
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)

class RuntimeContractTests(unittest.TestCase):
 def test_public_parent_is_delegated_before_both_layouts_publish(self):
  b=object.__new__(R.PortableBackend);b.c=types.SimpleNamespace(public_parent=mock.Mock(return_value=True));b.j=object()
  self.assertTrue(b.directory(R.PUBLIC));b.c.public_parent.assert_called_once_with(b.j,R.PUBLIC)

 def test_browser_startup_retry_keeps_the_original_http_budget(self):
  from contextlib import nullcontext
  response=mock.MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'original'
  opener=mock.Mock();opener.open.side_effect=[R.urllib.error.URLError(ConnectionRefusedError(R.errno.ECONNREFUSED,'starting')),response]
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R.time,'sleep')as pause:
   self.assertEqual(R.browser_route(opener,'https://192.168.2.7:8443/'),b'original')
   self.assertEqual(opener.open.call_count,2);pause.assert_called_once_with(.1)
   self.assertEqual(opener.open.call_args.kwargs['timeout'],12)
  self.assertIsNone(R.NoBrowserRedirect().redirect_request(None,None,302,'redirect',{},'http://127.0.0.1/'))

 def test_browser_never_retries_tls_or_authorization_and_rejects_late_body(self):
  from contextlib import nullcontext
  import email.message,io
  errors=[R.urllib.error.URLError(R.ssl.SSLCertVerificationError('bad TLS')),R.urllib.error.HTTPError('https://localhost/',403,'denied',email.message.Message(),io.BytesIO())]
  for error in errors:
   opener=mock.Mock();opener.open.side_effect=error
   with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R.time,'sleep')as pause:
    with self.assertRaises(type(error)):R.browser_route(opener,'https://192.168.2.7:8443/')
    pause.assert_not_called();self.assertEqual(opener.open.call_count,1)
  response=mock.MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'original'
  opener=mock.Mock();opener.open.return_value=response
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',side_effect=[5,5,17]):
   with self.assertRaises(TimeoutError):R.browser_route(opener,'https://192.168.2.7:8443/')

 @unittest.skipUnless(__import__('sys').platform=='linux'and __import__('os').geteuid()==0,'native restrictive umask')
 def test_native_public_parent_under_private_umask_and_foreign_parent_preserved(self):
  import os,tempfile,stat
  spec=importlib.util.spec_from_file_location('public_parent_engine',Path(__file__).with_name('aggregate-install.py'));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
  with tempfile.TemporaryDirectory(dir='/var/lib',prefix='droidmate-public-parent-')as folder:
   root=Path(folder);root.chmod(0o755);target=root/'public'
   with c.Journal(root/'journal',{'packageSha256':'a'*64,'layout':'portable'},create=True)as journal,mock.patch.object(c,'PUBLIC_PARENT',target):
    previous=os.umask(0o077)
    try:self.assertTrue(c.public_parent(journal,target))
    finally:os.umask(previous)
    self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o755)
    c.stage_public_tree(target,'b'*64,{'frontend/index.html':b'public'},lambda:True,create=True)
   foreign=root/'foreign';foreign.mkdir(mode=0o700)
   with c.Journal(root/'other',{'packageSha256':'c'*64,'layout':'portable'},create=True)as journal,mock.patch.object(c,'PUBLIC_PARENT',foreign):
    before=foreign.stat()
    with self.assertRaises(ValueError):c.public_parent(journal,foreign)
    self.assertEqual(stat.S_IMODE(foreign.stat().st_mode),0o700);self.assertEqual(foreign.stat().st_ino,before.st_ino)

 @unittest.skipUnless(__import__('sys').platform=='linux'and __import__('os').geteuid()==0,'native parent publication races')
 def test_native_replacement_before_open_and_public_target_collision_remain_private(self):
  import os,tempfile,stat
  spec=importlib.util.spec_from_file_location('public_parent_races',Path(__file__).with_name('aggregate-install.py'));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
  for replace_pending in (False,True):
   with tempfile.TemporaryDirectory(dir='/var/lib',prefix='droidmate-parent-race-')as folder:
    root=Path(folder);root.chmod(0o755);target=root/'public';real_open=os.open;foreign=[]
    with c.Journal(root/'journal',{'packageSha256':'a'*64,'layout':'portable'},create=True)as journal,mock.patch.object(c,'PUBLIC_PARENT',target):
     def racing_open(name,*args,**kw):
      if isinstance(name,str)and name.startswith('.droidmate-public-parent-')and not foreign:
       if replace_pending:
        original=root/name;original.rename(root/'original-claim');original.mkdir(mode=0o700);foreign.append(original)
       else:target.mkdir(mode=0o700);foreign.append(target)
      return real_open(name,*args,**kw)
     with mock.patch.object(c.os,'open',side_effect=racing_open):
      with self.assertRaises((ValueError,FileExistsError)):c.public_parent(journal,target)
     self.assertEqual(stat.S_IMODE(foreign[0].stat().st_mode),0o700)

 @unittest.skipUnless(__import__('sys').platform=='linux','native browser deadline')
 def test_native_browser_deadline_interrupts_blocking_body(self):
  import time,signal
  previous=signal.getsignal(signal.SIGALRM)
  with self.assertRaises(TimeoutError):
   with R.browser_deadline(time.monotonic()+.02):time.sleep(.2)
  self.assertEqual(signal.getsignal(signal.SIGALRM),previous);self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.,0.))

 def test_recovery_is_independently_enabled_ordered_after_original_runtime(self):
  ctx={'root':Path('/usr/local/share/droidmate-rc17-'+'a'*64),'release':{'packageSha256':'a'*64}}
  raw=R.recovery_unit(ctx).decode()
  self.assertIn('After=tesla-waydroid-runtime.service',raw)
  self.assertNotIn('Before=droidmate-gpu-default.service',raw)
  self.assertIn('ConditionPathExists=',raw)
  self.assertIn('--enrolled recover',raw)
  self.assertNotIn('Before=tesla-waydroid-runtime.service',raw)
  self.assertNotIn('RuntimeMaxSec',raw)
 def test_undo_inhibits_preference_without_replaying_retired_install_mapping(self):
  events=[]
  class Mapping:
   def __init__(self,key):self.key=key
   def prepare(self):pass
   def publish(self):
    if self.key=='preference':raise ValueError('retired install inhibition')
    events.append(self.key)
  backend=object.__new__(R.PortableBackend);backend.action='undo';backend.check=lambda:True
  backend.directory=lambda p:None;backend.map=lambda key:Mapping(key);backend.verify_preference=lambda:True
  backend.j=types.SimpleNamespace(root=Path('/synthetic'))
  def inhibit(mapping,*args):mapping.prepare();mapping.publish()
  backend.c=types.SimpleNamespace(TARGETS={k:Path('/synthetic')/k for k in ['gateway','preference','preference-restore']},OwnedBootLink=lambda j:Mapping('boot-link'),inhibit_preference=inhibit)
  self.assertTrue(backend.arm_recovery())
  self.assertIn('preference-restore',events);self.assertNotIn('preference',events)

 def test_effective_gateway_mapping_only_allows_original_plus_owned_override(self):
  base={**{key:'' for key in R.GRAPH},'FragmentPath':'/run/systemd/transient/tesla-waydroid-gateway-preview.service','DropInPaths':'','BindReadOnlyPaths':'','PrivateMounts':'no'}
  mapping='/etc/systemd/system/tesla-waydroid-gateway-preview.service.d/70-droidmate-aggregate.conf'
  actual={**base,'DropInPaths':mapping,'PrivateMounts':'yes','BindReadOnlyPaths':'/public:/opt/droiddeck/runtime:rbind'}
  self.assertTrue(R.gateway_graph(base,actual,mapping,'/public','/opt/droiddeck/runtime'))
  for change in [{'DropInPaths':mapping+' /foreign'},{'FragmentPath':'/etc/foreign'},{'BindReadOnlyPaths':'/foreign:/opt/droiddeck/runtime:rbind'}]:
   with self.assertRaises(ValueError):R.gateway_graph(base,{**actual,**change},mapping,'/public','/opt/droiddeck/runtime')
 def test_transaction_releases_original_lease_before_gpu_and_checks_after_health(self):
  events=[]
  class Capture:
   def restore(self):events.append('restore');return {}
   def release(self):
    class Lease:
     def __enter__(self):events.append('lease-enter');return {'check':lambda:True}
     def __exit__(self,*args):events.append('lease-exit')
    return Lease()
   def change(self,view):events.append('gpu');return {'phase':'accepted'}
  backend=object.__new__(R.PortableBackend);backend.capture=Capture();backend.lease=None;backend.lease_context=None
  backend.check=lambda:True;backend.cpu_check=lambda:True;backend.baseline={'viewToken':'a'*32};backend.rollback_mode=False
  backend.preference_cleanup=lambda:events.append('preference-cleanup')
  self.assertTrue(backend.restore_cpu());self.assertTrue(backend.prefer_gpu())
  self.assertEqual(events,['restore','lease-enter','lease-exit','gpu'])
 def test_cleanup_is_after_terminal_commit_and_resumes_without_rearming(self):
  events=[]
  class Map:
   def __init__(self,key):self.key=key
   def remove(self):events.append(self.key)
   def verify(self):return True
  backend=object.__new__(R.PortableBackend);backend.check=lambda:True
  backend.map=lambda key:Map(key);backend.reload=lambda:True
  backend.j=types.SimpleNamespace(state=lambda:'verified',root=Path('/synthetic'))
  backend.health=lambda original=False:True
  backend.ctx={};backend.c=types.SimpleNamespace(OwnedBootLink=lambda journal:Map('recovery-link'))
  backend.public_check=lambda:True
  with self.assertRaises(ValueError):backend.finish()
  self.assertEqual(events,[])
  backend.j.state=lambda:'accepted'
  with mock.patch.object(R.os.path,'lexists',return_value=True):self.assertTrue(backend.finish())
  self.assertEqual(events,['preference','preference-restore'])
  events.clear();backend.j.state=lambda:'restored'
  with mock.patch.object(R.os.path,'lexists',return_value=True):self.assertTrue(backend.finish())
  self.assertEqual(events,['preference','preference-restore','installer','recovery-link','recovery'])

 def test_gpu_admission_failure_accepts_only_verified_original_cpu_failsafe(self):
  events=[]
  backend=object.__new__(R.PortableBackend);backend.check=lambda:True;backend.cpu_check=lambda:True
  backend.baseline={'viewToken':'a'*32};backend.release_lease=lambda:events.append('lease-released')
  backend.capture=types.SimpleNamespace(change=mock.Mock(side_effect=ValueError('GPU unavailable')),
    restore=lambda:events.append('original-restore'),status=lambda:{'phase':'restored','mode':'cpu','updaterLeaseHeld':False})
  backend.health=lambda original=False:events.append('health') or True
  self.assertTrue(backend.prefer_gpu())
  self.assertEqual(events,['lease-released','original-restore','health'])
  backend.capture.status=lambda:{'phase':'uncertain'}
  with self.assertRaises(ValueError):backend.prefer_gpu()

 def test_stream_upgrade_requires_real_101_accept_and_never_reads_media_body(self):
  import hashlib,base64
  class Socket:
   def __init__(self,bad=False):self.sent=[];self.reply=b'';self.bad=bad;self.read=0
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def sendall(self,raw):
    self.sent.append(raw)
    if raw.startswith(b'GET '):
     key=[line.split(b': ',1)[1] for line in raw.split(b'\r\n') if line.startswith(b'Sec-WebSocket-Key:')][0]
     accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
     self.reply=b'HTTP/1.1 '+(b'503 Display Unavailable' if self.bad==503 else b'403 Forbidden' if self.bad else b'101 Switching Protocols')+b'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n'
   def recv(self,n):
    self.assert_header_only=n
    if not self.reply:raise AssertionError('Media body was read')
    self.read+=1;raw=self.reply[:n];self.reply=self.reply[n:];return raw
  for bad in (False,True,503):
   secured=Socket(bad);context=types.SimpleNamespace(wrap_socket=lambda stream,server_hostname:secured)
   with mock.patch.object(R.socket,'create_connection',return_value=Socket()):
    if bad:
     with self.assertRaises(R.MediaStarting if bad==503 else ValueError):R.stream_upgrade('192.168.1.7',8443,'/sockets/display?session='+'a'*64,context)
    else:self.assertTrue(R.stream_upgrade('192.168.1.7',8443,'/sockets/display?session='+'a'*64,context))
   self.assertEqual(secured.reply,b'')
  with self.assertRaises(ValueError):R.stream_upgrade('192.168.1.7',8443,'/foreign',context)

 def test_media_waits_for_transfer_and_typed_503_then_requires_real_upgrade(self):
  from contextlib import nullcontext
  import json
  def response(value):
   r=mock.MagicMock();r.__enter__.return_value=r;r.status=200;r.read.return_value=json.dumps(value).encode();return r
  opener=mock.Mock();opener.open.side_effect=[response(v)for v in [
   {'token':'a'*64},{'transactionStatus':'transferring','recoveryRequired':False},
   {'token':'b'*64},{'transactionStatus':'idle','recoveryRequired':False},
   {'token':'c'*64},{'transactionStatus':'idle','recoveryRequired':False}]]
  check=mock.Mock()
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R.time,'sleep')as pause,mock.patch.object(R,'stream_upgrade',side_effect=[R.MediaStarting('starting'),True])as upgrade,mock.patch.object(R,'audio_enabled',return_value=False):
   self.assertTrue(R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,check))
   self.assertEqual(upgrade.call_count,2);self.assertEqual(pause.call_args_list,[mock.call(1),mock.call(1)])
   self.assertIn('c'*64,upgrade.call_args.args[2]);self.assertEqual(check.call_count,4)
  self.assertEqual([a.args[0].get_method()for a in opener.open.call_args_list],['POST','GET']*3)

 def test_media_never_retries_failed_authority_auth_or_non503_upgrade(self):
  from contextlib import nullcontext
  import json
  def response(value):
   r=mock.MagicMock();r.__enter__.return_value=r;r.status=200;r.read.return_value=json.dumps(value).encode();return r
  for state in [{'transactionStatus':'authority_failed','recoveryRequired':False},
                {'transactionStatus':'recovery_required','recoveryRequired':True},
                {'transactionStatus':'idle','recoveryRequired':True},{'transactionStatus':'unknown','recoveryRequired':False}]:
   opener=mock.Mock();opener.open.side_effect=[response({'token':'a'*64}),response(state)]
   with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R,'stream_upgrade')as upgrade,mock.patch.object(R.time,'sleep')as pause:
    with self.assertRaises(ValueError):R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,lambda:True)
    upgrade.assert_not_called();pause.assert_not_called()
  opener=mock.Mock();opener.open.side_effect=[response({'token':'a'*64}),response({'transactionStatus':'idle','recoveryRequired':False})]
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R,'stream_upgrade',side_effect=ValueError('403')),mock.patch.object(R.time,'sleep')as pause:
   with self.assertRaises(ValueError):R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,lambda:True)
   pause.assert_not_called()
  opener=mock.Mock();opener.open.side_effect=R.urllib.error.URLError(R.ssl.SSLCertVerificationError('TLS'))
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R.time,'sleep')as pause:
   with self.assertRaises(R.urllib.error.URLError):R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,lambda:True)
   pause.assert_not_called()

 def test_final_original_admission_runs_after_network_alarm_is_disarmed(self):
  from contextlib import contextmanager
  import json
  active=[False];checks=[]
  @contextmanager
  def deadline(_):
   active[0]=True
   try:yield
   finally:active[0]=False
  def check():checks.append(active[0])
  def response(value):
   r=mock.MagicMock();r.__enter__.return_value=r;r.status=200;r.read.return_value=json.dumps(value).encode();return r
  opener=mock.Mock();opener.open.side_effect=[response({'token':'a'*64}),response({'transactionStatus':'idle','recoveryRequired':False})]
  with mock.patch.object(R,'browser_deadline',side_effect=deadline),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R,'stream_upgrade',return_value=True),mock.patch.object(R,'audio_enabled',return_value=False):
   self.assertTrue(R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,check))
  self.assertEqual(checks,[False,False])
  refuse_calls=[]
  def refuse():
   refuse_calls.append(True)
   if len(refuse_calls)==2:raise ValueError('Original final admission refused')
  opener.open.side_effect=[response({'token':'a'*64}),response({'transactionStatus':'idle','recoveryRequired':False})]
  with mock.patch.object(R,'browser_deadline',side_effect=deadline),mock.patch.object(R.time,'monotonic',return_value=5),mock.patch.object(R,'stream_upgrade',return_value=True),mock.patch.object(R,'audio_enabled',return_value=False):
   with self.assertRaisesRegex(ValueError,'final admission'):R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,refuse)

 def test_slow_original_admission_does_not_spend_cumulative_network_budget(self):
  from contextlib import contextmanager
  import json
  now=[5.];active=[False];budgets=[];calls=[]
  @contextmanager
  def alarm(deadline):
   budgets.append(deadline-now[0]);active[0]=True
   try:yield
   finally:active[0]=False
  def check():
   self.assertFalse(active[0]);now[0]+=100;calls.append(True)
  def pause(seconds):now[0]+=seconds
  def response(value):
   r=mock.MagicMock();r.__enter__.return_value=r;r.status=200;r.read.return_value=json.dumps(value).encode();return r
  opener=mock.Mock();opener.open.side_effect=[response(v)for v in [{'token':'a'*64},{'transactionStatus':'transferring','recoveryRequired':False},{'token':'b'*64},{'transactionStatus':'idle','recoveryRequired':False}]]
  with mock.patch.object(R,'browser_deadline',side_effect=alarm),mock.patch.object(R.time,'monotonic',side_effect=lambda:now[0]),mock.patch.object(R.time,'sleep',side_effect=pause),mock.patch.object(R,'stream_upgrade',return_value=True),mock.patch.object(R,'audio_enabled',return_value=False):
   self.assertTrue(R.browser_media(opener,'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,check))
  self.assertEqual(budgets,[90,89]);self.assertEqual(len(calls),3)

 def test_media_readiness_cannot_exceed_original_deadline(self):
  from contextlib import nullcontext
  with mock.patch.object(R,'browser_deadline',return_value=nullcontext()),mock.patch.object(R.time,'monotonic',return_value=95):
   with self.assertRaises(TimeoutError):R.browser_media(mock.Mock(),'https://192.168.1.7:8443','192.168.1.7',8443,object(),95,lambda:True)

 def test_audio_probe_reads_configuration_and_never_posts_audio_setting(self):
  import io,json
  seen=[]
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps({'browserAudioSupported':1,'persist.tesla-android.browser_audio.is_enabled':1}).encode()
  def open(request,timeout):
   seen.append((request.full_url,request.get_method()));return Response()
  self.assertTrue(R.audio_enabled(types.SimpleNamespace(open=open),'https://192.168.1.7:8443','a'*64))
  self.assertEqual(seen,[('https://192.168.1.7:8443/api/configuration','GET')])

 def test_fresh_foundation_continues_into_preserving_supervised_transaction(self):
  import contextlib
  events=[];release={'packageSha256':'a'*64};ctx={'root':Path('/usr/local/share/droidmate-rc17-'+'a'*64),'release':release,'check':lambda:True}
  material=types.SimpleNamespace(assets={})
  class Journal:
   def __init__(self,*a,**kw):self.root=Path('/synthetic')
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def state(self):return 'new'
  engine=types.SimpleNamespace(Journal=Journal)
  class StopAfterSupervision(Exception):pass
  class Supervisor:
   @staticmethod
   def is_worker():return False
   def __init__(self,*a):events.append('supervisor')
   def prepare(self,*a):events.append('owned-registration');raise StopAfterSupervision()
   def close(self):pass
  def source(ctx,name):
   if name.endswith('fresh-install.py'):
    return types.SimpleNamespace(fresh_install=lambda *a:events.append('fresh-core') or {'readyForCaptureEnrollment':True})
   if name.endswith('supervisor.py'):return types.SimpleNamespace(route_install=lambda *a:'launch',Supervisor=Supervisor)
   raise AssertionError(name)
  compiler=types.SimpleNamespace(retain=lambda c:c,materialize=lambda c:material,source_module=source)
  def exists(p):return p not in (R.CONFIG,'/opt/teslaandroid-waydroid/runtime')
  with mock.patch.object(R,'load_material',return_value=compiler),mock.patch.object(R.sys,'platform','linux'),mock.patch.object(R.os,'geteuid',return_value=0,create=True),mock.patch.object(R.os.path,'lexists',side_effect=exists):
   with self.assertRaises(StopAfterSupervision):R.main(['install-new','hilman','192.168.2.7'],ctx,engine)
  self.assertEqual(events,['fresh-core','supervisor','owned-registration'])

 def test_native_arguments_are_closed_before_admission(self):
  for args in [[],['other'],['recover','foreign'],['install-existing','extra']]:
   with self.assertRaises(ValueError):R.main(args,{},None)

if __name__=='__main__':unittest.main()
