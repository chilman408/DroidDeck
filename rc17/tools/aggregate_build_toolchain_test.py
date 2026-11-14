# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from contextlib import nullcontext
import hashlib, importlib.util, os, sys, tempfile, unittest
from types import SimpleNamespace
from unittest import mock

spec=importlib.util.spec_from_file_location('aggregate_build_toolchain',Path(__file__).with_name('aggregate-build-toolchain.py'))
T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)

class ToolchainTests(unittest.TestCase):
    def test_closed_original_command_and_private_namespace(self):
        command=T.original_build_command('tester');wrapped=T.build_command('tester',command)
        self.assertEqual(wrapped[:5],['/usr/bin/unshare','--mount','--propagation','private','--fork'])
        self.assertEqual(wrapped[-2:],['build','tester'])
        for bad in ([*command,'extra'],['/bin/sh'],[x.replace('tester','other')for x in command]):
            with self.assertRaises(ValueError):T.build_command('tester',bad)
        for name in ('root','../tester','tester;id','Tester','tester name'):
            with self.assertRaises(ValueError):T.original_build_command(name)

    def test_mount_refused_in_pid1_namespace_before_any_mount(self):
        with mock.patch.object(T,'system'),mock.patch.object(T,'check'),mock.patch.object(T,'installed_waydroid',return_value=T.WAYDROID_VERSION),mock.patch.object(T.os,'stat',return_value=SimpleNamespace(st_ino=5)),mock.patch.object(T,'run')as run,mock.patch.object(T.os,'execve')as execute:
            with self.assertRaises(ValueError):T.build('tester')
            run.assert_not_called();execute.assert_not_called()

    def test_only_readonly_private_jdk_then_exact_unprivileged_builder(self):
        with mock.patch.object(T,'system'),mock.patch.object(T,'check'),mock.patch.object(T,'installed_waydroid',return_value=T.WAYDROID_VERSION),mock.patch.object(T.os,'stat',side_effect=[SimpleNamespace(st_ino=2),SimpleNamespace(st_ino=1)]),mock.patch.object(T,'run',side_effect=[b'',b'',b'javac 17.0.20.1\n'])as run,mock.patch.object(T.os,'execve',side_effect=SystemExit)as execute:
            with self.assertRaises(SystemExit):T.build('tester')
            self.assertEqual(run.call_args_list[0].args[0],['/usr/bin/mount','--bind',str(T.STATE/'extracted'/T.JDK.relative_to('/')),str(T.JDK)])
            self.assertEqual(run.call_args_list[1].args[0],['/usr/bin/mount','-o','remount,bind,ro',str(T.JDK)])
            self.assertEqual(execute.call_args.args,('/usr/sbin/runuser',T.original_build_command('tester'),T.ENV))

    def test_wrong_real_compiler_never_executes_builder(self):
        with mock.patch.object(T,'system'),mock.patch.object(T,'check'),mock.patch.object(T,'installed_waydroid',return_value=T.WAYDROID_VERSION),mock.patch.object(T.os,'stat',side_effect=[SimpleNamespace(st_ino=2),SimpleNamespace(st_ino=1)]),mock.patch.object(T,'run',side_effect=[b'',b'',b'javac 17.0.21.0.3\n']),mock.patch.object(T.os,'execve')as execute:
            with self.assertRaises(ValueError):T.build('tester')
            execute.assert_not_called()

    def test_download_digest_checked_before_extraction_or_completion(self):
        with tempfile.TemporaryDirectory()as d,mock.patch.object(T,'system'),mock.patch.object(T,'STATE',Path(d)/'new'),mock.patch.object(T,'PACKAGES',(('jdk',3,hashlib.sha256(b'yes').hexdigest()),)),mock.patch.object(T,'regular',return_value=b'bad'),mock.patch.object(T,'run')as run:
            with self.assertRaises(ValueError):T.prepare()
            self.assertFalse((Path(d)/'new/complete.json').exists())
            self.assertEqual(run.call_count,1)
            self.assertNotIn('dpkg-deb',str(run.call_args))
            self.assertIn('--max-filesize',run.call_args.args[0])

    def test_package_pair_is_original_signed_ubuntu_version(self):
        self.assertEqual(T.VERSION,'17.0.20.1+1-1~26.04')
        self.assertEqual([x[1]for x in T.PACKAGES],[71779420,48133612])
        self.assertEqual(T.SNAPSHOT,'https://snapshot.ubuntu.com/ubuntu/20261015T000000Z/')
        self.assertEqual(len({x[2]for x in T.PACKAGES}),2)

    def test_empty_source_remains_bound_to_the_exact_manifest_hash(self):
        import json
        manifest=json.dumps({'files':[{'path':'src/tools/__init__.py','size':0,'sha256':hashlib.sha256(b'').hexdigest()}]+[{'path':'f'+str(i),'size':1,'sha256':hashlib.sha256(b'x').hexdigest()}for i in range(843)]}).encode()
        def read(path,limit,**options):
            if Path(path).name=='package-manifest.json':return manifest
            self.assertTrue(options.get('allow_empty'))
            return b'' if Path(path).name=='__init__.py'else b'x'
        with mock.patch.object(T,'FOUNDATION_MANIFEST',hashlib.sha256(manifest).hexdigest()),mock.patch.object(T,'regular',side_effect=read):
            self.assertEqual(T.foundation_sources(),844)
        def truncated(path,limit,**options):return manifest if Path(path).name=='package-manifest.json'else b''
        with mock.patch.object(T,'FOUNDATION_MANIFEST',hashlib.sha256(manifest).hexdigest()),mock.patch.object(T,'regular',side_effect=truncated):
            with self.assertRaises(ValueError):T.foundation_sources()

    def test_waydroid_pin_does_not_allow_existing_different_package(self):
        T.compatible_waydroid(None);T.compatible_waydroid('1.6.2')
        for version in ('1.6.3','1.6.1','1.6.2+changed'):
            with self.assertRaises(ValueError):T.compatible_waydroid(version)
        self.assertEqual(T.WAYDROID_PIN_BYTES,b'Package: waydroid\nPin: version 1.6.2\nPin-Priority: 1001\n')
        with mock.patch.object(T,'system'),mock.patch.object(T,'installed_waydroid',return_value='1.6.3'),mock.patch.object(T,'protected_parent')as parent:
            with self.assertRaises(ValueError):T.install_waydroid_pin()
            parent.assert_not_called()

    @unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid')and os.geteuid()==0,'native owned package preference')
    def test_native_pin_ownership_foreign_replacement_and_no_replay(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-package-pin-',dir='/var/lib')as d:
            root=Path(d);state=root/'state';state.mkdir(mode=0o700);preferences=root/'preferences';preferences.mkdir(mode=0o755);target=preferences/'waydroid'
            with mock.patch.object(T,'STATE',state),mock.patch.object(T,'WAYDROID_PIN',target),mock.patch.object(T,'installed_waydroid',return_value=None):
                T.install_waydroid_pin();T.check_waydroid_pin()
                self.assertEqual(target.stat().st_nlink,2)
                anchor=preferences/'.droidmate-waydroid'/'preference'
                self.assertEqual(anchor.stat().st_dev,preferences.stat().st_dev)
                self.assertEqual(anchor.stat().st_ino,target.stat().st_ino)
                self.assertFalse((state/'waydroid-pin').exists())
                with self.assertRaises(ValueError):T.install_waydroid_pin()
                target.unlink();target.write_bytes(T.WAYDROID_PIN_BYTES);target.chmod(0o644)
                with self.assertRaises(ValueError):T.check_waydroid_pin()
                with self.assertRaises(ValueError):T.install_waydroid_pin()
                self.assertEqual(target.read_bytes(),T.WAYDROID_PIN_BYTES)

    def test_runtime_readiness_uses_remaining_original_budget_and_keeps_command(self):
        instance=SimpleNamespace(phase='30-start-runtime',config={'user':'tester','lanAddress':'192.168.2.7'})
        command=['/usr/bin/systemctl','start','tesla-waydroid-runtime.service'];original=mock.Mock(return_value=b'original')
        with mock.patch.object(T.time,'monotonic',return_value=10),mock.patch.object(T,'wait_runtime_ready')as ready:
            self.assertEqual(T.foundation_run(original,instance,command,'tester','192.168.2.7',timeout=150),b'original')
            ready.assert_called_once_with(instance.config,160)
            original.assert_called_once_with(instance,command,data=None,timeout=150)
        for bad in (command+['extra'],['/bin/sh']):
            original.reset_mock()
            with self.assertRaises(ValueError):T.foundation_run(original,instance,bad,'tester','192.168.2.7',timeout=150)
            original.assert_not_called()

    def test_readiness_retries_only_listener_and_explicit_busy_then_verifies_session(self):
        import io,urllib.error,email.message
        config={'home':'/home/tester','lanAddress':'192.168.2.7','port':8443}
        headers=email.message.Message();headers['Retry-After']='1'
        busy=urllib.error.HTTPError('https://192.168.2.7:8443/',503,'busy',headers,io.BytesIO(b''))
        response=mock.MagicMock();response.status=200;response.read.return_value=b'{"token":"'+b'a'*64+b'"}'
        response.__enter__.return_value=response
        opener=mock.Mock();opener.open.side_effect=[urllib.error.URLError(ConnectionRefusedError(T.errno.ECONNREFUSED,'starting')),busy,response]
        with mock.patch.object(T,'runtime_deadline',return_value=nullcontext()),mock.patch.object(T.ssl,'create_default_context')as tls,mock.patch.object(T.urllib.request,'build_opener',return_value=opener),mock.patch.object(T.time,'monotonic',return_value=149),mock.patch.object(T.time,'sleep')as pause:
            self.assertTrue(T.wait_runtime_ready(config,150));self.assertEqual(pause.call_count,2)
            tls.assert_called_once_with(cafile=str(Path('/home/tester')/'.config/droiddeck/cert.pem'))
            self.assertTrue(all(c.kwargs['timeout']==1 for c in opener.open.call_args_list))

    def test_readiness_never_retries_tls_authorization_bad_session_or_elapsed_budget(self):
        import io,urllib.error,email.message
        config={'home':'/home/tester','lanAddress':'192.168.2.7','port':8443}
        failures=[urllib.error.URLError(T.ssl.SSLCertVerificationError('bad TLS')),
            urllib.error.HTTPError('https://192.168.2.7:8443/',403,'denied',email.message.Message(),io.BytesIO(b''))]
        for failure in failures:
            opener=mock.Mock();opener.open.side_effect=failure
            with mock.patch.object(T,'runtime_deadline',return_value=nullcontext()),mock.patch.object(T.ssl,'create_default_context'),mock.patch.object(T.urllib.request,'build_opener',return_value=opener),mock.patch.object(T.time,'monotonic',return_value=1),mock.patch.object(T.time,'sleep')as pause:
                with self.assertRaises(type(failure)):T.wait_runtime_ready(config,150)
                pause.assert_not_called();self.assertEqual(opener.open.call_count,1)
        response=mock.MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'{"token":"bad"}'
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(T,'runtime_deadline',return_value=nullcontext()),mock.patch.object(T.ssl,'create_default_context'),mock.patch.object(T.urllib.request,'build_opener',return_value=opener),mock.patch.object(T.time,'monotonic',return_value=150),mock.patch.object(T.time,'sleep')as pause:
            with self.assertRaises(TimeoutError):T.wait_runtime_ready(config,150)
            opener.open.assert_not_called();pause.assert_not_called()
        with mock.patch.object(T,'runtime_deadline',return_value=nullcontext()),mock.patch.object(T.ssl,'create_default_context'),mock.patch.object(T.urllib.request,'build_opener',return_value=opener),mock.patch.object(T.time,'monotonic',return_value=1),mock.patch.object(T.time,'sleep')as pause:
            with self.assertRaises(ValueError):T.wait_runtime_ready(config,150)
            pause.assert_not_called()

    def test_runtime_session_probe_disallows_redirects_and_late_success(self):
        self.assertIsNone(T.NoRuntimeRedirect().redirect_request(None,None,302,'redirect',{},'http://127.0.0.1/'))
        response=mock.MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=b'{"token":"'+b'a'*64+b'"}'
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(T,'runtime_deadline',return_value=nullcontext()),mock.patch.object(T.ssl,'create_default_context'),mock.patch.object(T.urllib.request,'build_opener',return_value=opener)as factory,mock.patch.object(T.time,'monotonic',side_effect=[1,150]):
            with self.assertRaises(TimeoutError):T.wait_runtime_ready({'home':'/home/tester','lanAddress':'192.168.2.7','port':8443},150)
            self.assertTrue(any(isinstance(x,T.NoRuntimeRedirect)for x in factory.call_args.args))

    @unittest.skipUnless(sys.platform=='linux','native absolute installer deadline')
    def test_absolute_deadline_interrupts_blocked_body_and_restores_signal_state(self):
        import signal,time
        previous=signal.getsignal(signal.SIGALRM);start=time.monotonic()
        with self.assertRaises(TimeoutError):
            with T.runtime_deadline(start+.03):time.sleep(.3)
        self.assertLess(time.monotonic()-start,.2)
        self.assertEqual(signal.getsignal(signal.SIGALRM),previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.,0.))
        signal.setitimer(signal.ITIMER_REAL,10)
        try:
            with self.assertRaises(ValueError):
                with T.runtime_deadline(time.monotonic()+1):self.fail('Existing timer adopted')
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0],9)
        finally:signal.setitimer(signal.ITIMER_REAL,0)

    def test_closed_actions_refuse_before_platform_or_network(self):
        with mock.patch.object(T,'foundation')as foundation,mock.patch.object(T,'build')as build:
            for args in ([],['prepare'],['foundation','tester'],['build','tester','extra']):
                with self.assertRaises(ValueError):T.main(args)
            foundation.assert_not_called();build.assert_not_called()

    @unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid')and os.geteuid()==0,'native protected filesystem')
    def test_actual_protected_read_refuses_link_or_modified_mode(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-toolchain-',dir='/var/lib')as d:
            p=Path(d)/'input';p.write_bytes(b'public');p.chmod(0o600)
            self.assertEqual(T.regular(p,6),b'public')
            empty=Path(d)/'empty';empty.write_bytes(b'');empty.chmod(0o600)
            with self.assertRaises(ValueError):T.regular(empty,1)
            self.assertEqual(T.regular(empty,1,allow_empty=True),b'')
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):T.regular(link,6)
            p.chmod(0o666)
            with self.assertRaises(ValueError):T.regular(p,6)

if __name__=='__main__':unittest.main()
