# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import types
import stat,json
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('portable_bridge',Path(__file__).with_name('aggregate-portable-capture.py'))
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)

def ctx():return {'root':Path('/usr/local/share/droidmate-rc17-'+'a'*64),'release':{
    'kind':'droidmate-aggregate-release-authorization','aggregateRelease':'RC17','coreRuntimeVersion':'0.1.1-rc.20',
    'compatibleReleaseSha256':P.CORE,'activationAllowed':True,'packageSha256':'a'*64},'check':lambda:True}
def report():return {'kind':'verified-installed-pilot-release','version':'0.1.1-rc.20','signedBundleSha256':P.CORE,
    'signedReleaseVerified':True,'installedCodeOwnershipVerified':True,'updateLockShared':True,'servicesChanged':False}

class PortableCaptureTests(unittest.TestCase):
    def fake(self):
        p=P.Capture(ctx());p.ctx['check']();p.owner=lambda:None
        p.c={'check':lambda:True};p.v=types.SimpleNamespace(SOCKET=Path('/synthetic/control.sock'),ROOT=Path('/synthetic'))
        p.status=mock.Mock(return_value={'phase':'restored','mode':'cpu','updaterLeaseHeld':False})
        @contextmanager
        def release():yield {'report':report(),'check':report}
        p.r=types.SimpleNamespace(hold_release=release);return p

    def test_pending_status_never_hides_a_foreign_package(self):
        p=self.fake();p.status=P.Capture.status.__get__(p)
        operation={'packageSha256':P.PACKAGE,'phase':'restoring'}
        class Tree:
            def __init__(self,stack):pass
            def file(self,*args):return json.dumps(operation).encode()
            def check(self):pass
        p.b=types.SimpleNamespace(Tree=Tree,document=json.loads);p.v.record=lambda v:v
        def exists(path):return path!=p.v.SOCKET
        with mock.patch.object(P.os.path,'lexists',side_effect=exists):
            with self.assertRaises(P.CaptureRecoveryPending):p.status()
            operation['packageSha256']='f'*64
            with self.assertRaises(ValueError)as failure:p.status()
            self.assertNotIsInstance(failure.exception,P.CaptureRecoveryPending)

    def test_context_is_bound_before_callbacks_and_changes_refused(self):
        c=ctx();binding=P.retain(c);self.assertTrue(binding['check']())
        c['root']=Path('/usr/local/share/droidmate-rc17-'+'b'*64);c['release']['packageSha256']='b'*64
        with self.assertRaises(ValueError):binding['check']()
        c=ctx()
        def mutate():c['release']['aggregateRelease']='RC18';return True
        c['check']=mutate
        with self.assertRaises(ValueError):P.retain(c)

    def test_original_enrollment_guard_returns_none_on_success(self):
        p=self.fake();p.owner=lambda:None
        self.assertTrue(p.check())
        p.owner=lambda:False
        with self.assertRaises(ValueError):p.check()

    def test_original_core_report_requires_exact_base_and_flags(self):
        self.assertTrue(P.require_release(report()))
        for key,value in [('version','0.1.1-rc.17'),('signedBundleSha256','b'*64),('signedReleaseVerified',False),
                          ('installedCodeOwnershipVerified',False),('updateLockShared',False),('servicesChanged',True)]:
            with self.assertRaises(ValueError):P.require_release({**report(),key:value})

    def test_live_socket_is_package_and_operation_bound_before_request(self):
        p=self.fake();p.status=P.Capture.status.__get__(p);p.v.client=mock.Mock()
        p.owned_socket=mock.Mock(side_effect=ValueError('Foreign live package'))
        with mock.patch.object(P.os.path,'lexists',return_value=True):
            with self.assertRaises(ValueError):p.status()
        p.v.client.assert_not_called()

    def test_protected_operation_package_socket_and_boot_must_all_match(self):
        p=self.fake();boot='11111111-1111-1111-1111-111111111111'
        op={'packageSha256':P.PACKAGE,'phase':'accepted','boot':boot,'socketIdentity':[1],'token':'a'*32,'viewToken':'b'*32}
        class Tree:
            def __init__(self,stack):pass
            def directory(self,path):return 11
            def file(self,path,limit):return json.dumps(op).encode()
            def check(self):pass
        p.b=types.SimpleNamespace(Tree=Tree,document=json.loads)
        p.v.record=lambda value:value;p.v.pin=lambda value:[1]
        sock=types.SimpleNamespace(st_mode=stat.S_IFSOCK|0o600,st_uid=0,st_gid=0,st_nlink=1)
        with mock.patch.object(P.os,'fstat',return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700)),mock.patch.object(Path,'lstat',return_value=sock),mock.patch.object(Path,'read_text',return_value=boot):
            self.assertEqual(p.owned_socket()['token'],'a'*32)
            for key,value in [('packageSha256','f'*64),('phase','restored'),('boot','older'),('socketIdentity',[2])]:
                before=op[key];op[key]=value
                with self.assertRaises(ValueError):p.owned_socket()
                op[key]=before

    def test_cpu_socket_state_is_rechecked_under_original_acquired_lease(self):
        p=self.fake();socket=[False];entered=[]
        @contextmanager
        def lease():
            socket[0]=True
            yield {'report':report(),'check':report}
        p.r.hold_release=lease
        with mock.patch.object(P.os.path,'lexists',side_effect=lambda path:socket[0]):
            with self.assertRaises(ValueError):
                with p.release():entered.append(True)
        self.assertEqual(entered,[])

    def test_original_lease_requires_cpu_no_socket_and_releases_on_error(self):
        p=self.fake()
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            with p.release() as original:
                self.assertTrue(p.lease);self.assertTrue(original['check']())
                with self.assertRaises(ValueError):p.change('b'*32)
            self.assertFalse(p.lease)
            with self.assertRaises(RuntimeError):
                with p.release():raise RuntimeError('injected operation interruption')
            self.assertFalse(p.lease)
        p.status.return_value={'phase':'accepted','mode':'gpu','updaterLeaseHeld':True}
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            with self.assertRaises(ValueError):
                with p.release():pass

    def test_restore_waits_for_socket_and_original_updater_lease(self):
        p=self.fake();clock=[0];attempts=[0];p.clock=lambda:clock[0];p.pause=lambda delay:clock.__setitem__(0,clock[0]+delay)
        @contextmanager
        def lease():
            attempts[0]+=1
            if attempts[0]<3:raise BlockingIOError('old owner still exiting')
            yield {'report':report(),'check':report}
        p.r.hold_release=lease
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            result=p.restore(deadline_seconds=2)
        self.assertEqual(attempts[0],3);self.assertTrue(result['originalLeaseAvailable']);self.assertFalse(p.lease)

    def test_restore_waits_when_recovery_changes_between_cpu_probe_and_lease(self):
        p=self.fake();clock=[0];calls=[0]
        p.clock=lambda:clock[0];p.pause=lambda delay:clock.__setitem__(0,clock[0]+delay)
        def status():
            calls[0]+=1
            if calls[0]==2:raise P.CaptureRecoveryPending('original controller still recovering')
            return {'phase':'restored','mode':'cpu','updaterLeaseHeld':False}
        p.status=mock.Mock(side_effect=status)
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            value=p.restore(deadline_seconds=2)
        self.assertTrue(value['originalLeaseAvailable']);self.assertGreater(clock[0],0)
        self.assertFalse(p.lease)

    def test_pending_recovery_is_bounded_and_never_dispatched_or_accepted(self):
        p=self.fake();clock=[0]
        p.clock=lambda:clock[0];p.pause=lambda delay:clock.__setitem__(0,clock[0]+delay)
        p.status=mock.Mock(side_effect=P.CaptureRecoveryPending('pending'))
        p.v.dispatch=mock.Mock();p.v.client=mock.Mock()
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            with self.assertRaises(TimeoutError):p.restore(deadline_seconds=.5)
        p.v.dispatch.assert_not_called();p.v.client.assert_not_called();self.assertFalse(p.lease)

    def test_restore_and_change_cannot_cross_false_admission(self):
        p=self.fake();p.ctx['check']=lambda:False
        with mock.patch.object(P.os.path,'lexists',return_value=False):
            with self.assertRaises(ValueError):p.restore()
            with self.assertRaises(ValueError):p.change('b'*32)

    def test_fresh_change_delegates_once_and_does_not_replay_preference(self):
        p=self.fake();p.status.side_effect=[{'phase':'restored','updaterLeaseHeld':False},
            {'phase':'accepted','mode':'gpu','updaterLeaseHeld':True}]
        p.v.dispatch=mock.Mock(return_value={'phase':'accepted'})
        with mock.patch.object(P.os.path,'lexists',return_value=False):self.assertEqual(p.change('b'*32),{'phase':'accepted'})
        p.v.dispatch.assert_called_once_with(p.c,'change',['b'*32])

    def test_readonly_status_never_invokes_orphan_dispatch(self):
        p=self.fake();p.status=P.Capture.status.__get__(p);p.v.client=mock.Mock(return_value={'phase':'accepted','mode':'gpu','updaterLeaseHeld':True})
        p.v.dispatch=mock.Mock();p.owned_socket=mock.Mock(return_value={'token':'a'*32})
        with mock.patch.object(P.os.path,'lexists',return_value=True):self.assertTrue(P.gpu_status(p.status()))
        p.v.client.assert_called_once_with('status');p.v.dispatch.assert_not_called()
        with mock.patch.object(P.os.path,'lexists',return_value=False):self.assertTrue(P.cpu_status(p.status()))
        p.v.dispatch.assert_not_called()

if __name__=='__main__':unittest.main()
