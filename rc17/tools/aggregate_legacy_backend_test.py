# SPDX-License-Identifier: GPL-3.0-only
"""Legacy transaction and dispatch tests. All OS/service effects are simulated."""
from contextlib import ExitStack
import copy
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import types
import unittest
from unittest import mock


def load(name):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B, C, R, K, L = (load(n) for n in ('aggregate-legacy-backend.py', 'aggregate-install.py',
    'aggregate-runtime.py', 'aggregate-legacy-runtime.py', 'aggregate-legacy-video.py'))
DIGEST = 'a' * 64


def ctx():
    return {'root': Path('/usr/local/share/droidmate-rc17-' + DIGEST), 'release': {
        'kind': 'droidmate-aggregate-release-authorization', 'aggregateRelease': 'RC17',
        'coreRuntimeVersion': '0.1.1-rc.20', 'compatibleReleaseSha256': L.CORE,
        'packageSha256': DIGEST, 'activationAllowed': True}, 'check': lambda: True}


def graph(unit):
    return {**dict.fromkeys(R.GRAPH, ''), 'PrivateMounts': 'no',
            'FragmentPath': ('/etc/systemd/system/' if unit == R.RUNTIME else '/run/systemd/transient/') + unit}


class Journal:
    def __init__(self, phase='new'):
        self.phase, self.records, self.events = phase, {}, []
        self.root = R.ROOT / DIGEST
        self.intent = {'transactionToken': 'b' * 32}
    def state(self):
        return self.phase
    def append(self, phase):
        C.transition(self.phase, phase)
        self.phase = phase
        self.events.append('durable:' + phase)
    def read(self, name):
        return copy.deepcopy(self.records[name])
    def write(self, name, value):
        if name in self.records:
            raise ValueError('No overwrite')
        self.records[name] = copy.deepcopy(value)
    def guard(self):
        return True


class Host:
    """OS boundary only: actual LegacyBackend and C.transact drive this model."""
    def __init__(self, journal):
        self.j, self.live, self.lease = journal, True, True
        self.maps, self.retired, self.prepared, self.files = set(), set(), set(), set()
        self.events = journal.events
        self.config = {'lanAddress': '192.168.2.90', 'port': 8443}
    def exists(self, path):
        path = Path(path)
        if path == B.NATIVE_LEASE:
            return self.lease
        if path.parent == self.j.root:
            return path.name in self.j.records
        return path in C.TARGETS.values() and any(C.TARGETS.get(k) == path for k in self.maps)
    def command(self, args, timeout=30):
        op = args[1]
        self.events.append(op)
        if op == 'stop':
            self.assert_runtime(args)
            self.live, self.lease = False, False
            self.events.append('QS-descendants-drained')
        elif op == 'start':
            self.assert_runtime(args)
            self.live, self.lease = True, True
    def assert_runtime(self, args):
        if args != ['/usr/bin/systemctl', args[1], R.RUNTIME]:
            raise AssertionError('Independent desktop or worker operation attempted')
    def activity(self, unit):
        return {'LoadState': 'loaded', 'ActiveState': 'active' if self.live else 'inactive', 'SubState': 'running' if self.live else 'dead',
                'MainPID': '42' if self.live else '0', 'ControlPID': '0', 'Job': ''}
    def mapping(self, key):
        host = self
        class Mapping:
            def prepare(self):
                host.events.append('prepare:' + key)
                if key not in host.prepared:
                    host.j.records['map-' + key + '.json'] = {'owned': key}
                    host.prepared.add(key)
            def publish(self):
                if key in host.retired:
                    raise ValueError('Retired map replay')
                host.maps.add(key)
                host.events.append('publish:' + key)
            def verify(self):
                return key in host.maps
            def remove(self):
                host.maps.discard(key)
                host.retired.add(key)
                host.events.append('retire:' + key)
        return Mapping()


def backend(action='install', phase='new'):
    j = Journal(phase)
    host = Host(j)
    b = B.LegacyBackend.__new__(B.LegacyBackend)
    b.ctx, b.j, b.action = ctx(), j, action
    b.closed, b.stack, b.maps = False, ExitStack(), {}
    b.video_mode = "cpu"
    b.rollback_mode = action != 'install'
    b.c = types.SimpleNamespace(TARGETS=C.TARGETS, OwnedBootLink=lambda _: host.mapping('recovery-link'))
    def stage(parent, digest, files, check, create=False):
        check()
        host.events.append('public:' + digest)
        host.files.add(digest)
    b.c.stage_public_tree = stage
    b.view = {'runtimeDigest': DIGEST, 'frontendDigest': 'c' * 64, 'runtimeFiles': {'legacy/runtime/x.js': b'x'},
              'frontendFiles': {'frontend/index.html': b'page'}, 'mapping': b'gateway'}
    b.material = types.SimpleNamespace(frontend={'index.html': b'page'})
    b.baseline = {'config': host.config, 'gateway': graph(R.GATEWAY), 'video': graph(B.VIDEO)}
    b.r = types.SimpleNamespace(PUBLIC=R.PUBLIC, GRAPH=R.GRAPH, command=host.command)
    b.check = lambda: True
    b.establish = lambda: host.events.append('establish') or True
    b.map = host.mapping
    b.directory = lambda path: host.events.append('directory')
    b.activity = host.activity
    def health(original=False):
        host.events.append('health:original' if original else 'health:mapped')
        if not host.live:
            raise ValueError('Unhealthy stopped runtime')
        if original and host.maps & {'gateway', 'video'}:
            raise ValueError('Original health with live mappings')
        if not original and not {'gateway', 'video'} <= host.maps:
            raise ValueError('Missing owned mappings')
        return True
    b.health = health
    b.video_check = lambda original=False: health(original=original)
    return b, host


class TransactionTests(unittest.TestCase):
    def test_new_public_parent_uses_same_owned_public_creator_as_portable(self):
        value=object.__new__(B.LegacyBackend);value.r=R;value.j=object();value.c=types.SimpleNamespace(public_parent=mock.Mock(return_value=True))
        self.assertTrue(value.directory(R.PUBLIC));value.c.public_parent.assert_called_once_with(value.j,R.PUBLIC)

    def test_actual_backend_install_then_undo_preserves_original_and_durable_cleanup(self):
        b, h = backend()
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            self.assertEqual(C.transact(b.j, b, b.check, 'install')['phase'], 'accepted')
            b.finish()
            self.assertTrue({'gateway', 'video', 'recovery', 'recovery-link'} <= h.maps)
            self.assertLess(h.events.index('QS-descendants-drained'), h.events.index('publish:gateway'))
            self.assertLess(h.events.index('publish:video'), h.events.index('start'))
            self.assertEqual(h.events.count('start'), 1)
            self.assertNotIn('retire:recovery', h.events)
            b.action = 'undo'
            b.rollback_mode = True
            self.assertEqual(C.transact(b.j, b, b.check, 'undo')['phase'], 'restored')
            b.finish()
        self.assertFalse(h.maps)
        self.assertLess(h.events.index('durable:restored'), h.events.index('retire:recovery'))
        self.assertLess(h.events.index('durable:recovering'), h.events.index('retire:video'))
        self.assertLess(h.events.index('retire:video'), h.events.index('retire:gateway'))
        self.assertEqual(h.events.count('QS-descendants-drained'), 3)  # Original stop plus two idempotent recovery stops.

    def test_recovery_from_partial_mapping_stops_drains_before_retirement(self):
        b, h = backend('recover', 'mapped')
        for key in ('gateway', 'video'):
            h.mapping(key).prepare()
            h.mapping(key).publish()
        h.events.clear()
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            self.assertEqual(C.transact(b.j, b, b.check, 'recover')['phase'], 'restored')
            b.finish()
        self.assertLess(h.events.index('QS-descendants-drained'), h.events.index('retire:video'))
        self.assertEqual(h.events[-1], 'daemon-reload')

    def test_failed_supervisor_stop_is_cleared_only_after_complete_worker_and_lease_drain(self):
        for blocked in (None,'worker','lease','job','reset-not-effective'):
            b,h=backend('recover','recovering');h.live=False;h.lease=blocked=='lease';reset=[]
            original=h.activity
            def activity(unit):
                value=original(unit)
                if unit==B.RUNTIME and (not reset or blocked=='reset-not-effective'):
                    value={**value,'ActiveState':'failed','SubState':'failed'}
                if unit==B.VIDEO and blocked=='worker':value={**value,'MainPID':'7'}
                if unit==B.VIDEO and blocked=='job':value={**value,'Job':'42'}
                return value
            def command(args,timeout=30):
                if args[1]=='reset-failed':
                    self.assertEqual(args,['/usr/bin/systemctl','reset-failed',B.RUNTIME]);reset.append(True)
            b.activity=activity;b.r.command=command
            with mock.patch.object(B.os.path,'lexists',side_effect=h.exists):
                if blocked:
                    with self.assertRaises(ValueError):b.stop()
                else:self.assertTrue(b.stop())
            self.assertEqual(bool(reset),blocked in (None,'reset-not-effective'))

    def test_live_native_lease_or_unstopped_worker_blocks_mapping_changes(self):
        b, h = backend()
        h.live = False
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            with self.assertRaisesRegex(ValueError, 'lease'):
                b.publish()
        h.lease = False
        original = b.activity
        b.activity = lambda unit: {**original(unit), 'MainPID': '9' if unit.endswith('api-preview.service') else '0'}
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            with self.assertRaisesRegex(ValueError, 'fully stopped'):
                b.publish()
        self.assertFalse(h.maps)
        self.assertFalse(h.files)

    def test_foreign_mapping_is_preserved_during_rollback(self):
        b, h = backend('recover', 'recovering')
        h.live, h.lease = False, False
        h.maps.add('video')  # No durable ownership record.
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            with self.assertRaisesRegex(ValueError, 'Foreign'):
                b.rollback()
        self.assertIn('video', h.maps)
        self.assertNotIn('retire:video', h.events)

    def test_cleanup_requires_terminal_state_and_terminal_reentry_does_not_rearm(self):
        b, h = backend('recover', 'verified')
        b.health = mock.Mock(return_value=True)
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            with self.assertRaises(ValueError):
                b.finish()
            b.j.phase = 'restored'
            h.prepared.update({'recovery', 'recovery-link', 'installer'})
            for key in h.prepared:
                b.j.records['map-' + key + '.json'] = {'owned': key}
            b.finish()
            b.finish()
        self.assertFalse(any(e.startswith('publish:') for e in h.events))

    def test_unstarted_recovery_retires_only_own_controls_without_runtime_stop(self):
        b, h = backend('recover')
        for key in ('recovery', 'recovery-link', 'installer'):
            h.mapping(key).prepare()
            h.mapping(key).publish()
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            self.assertEqual(b.restore_unstarted(), {'phase': 'restored', 'servicesChanged': False})
        self.assertFalse(h.maps)
        self.assertNotIn('stop', h.events)
        self.assertNotIn('start', h.events)

    def test_failed_health_leaves_recovery_armed_and_transaction_unaccepted(self):
        b, h = backend()
        original = b.health
        def health(original=False):
            if not original:
                raise ValueError('Authenticated media upgrade failed')
            return True
        b.health = health
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            with self.assertRaisesRegex(ValueError, 'media'):
                C.transact(b.j, b, b.check, 'install')
        self.assertEqual(b.j.state(), 'started')
        self.assertIn('recovery', h.maps)
        self.assertNotIn('durable:accepted', h.events)

    def test_missing_services_after_intent_is_not_recreated(self):
        b, h = backend('recover', 'intent')
        del b.establish
        b.k = types.SimpleNamespace(establish=mock.Mock(return_value=True))
        b.m = None
        b.baseline = None
        with self.assertRaisesRegex(ValueError, 'cannot be recreated'):
            b.establish()
        self.assertNotIn('services.json', b.j.records)

    def test_origin_is_original_https_lan_and_never_loopback_auth_bypass(self):
        self.assertEqual(B.origin_config('https://192.168.2.90:8443'), {'lanAddress': '192.168.2.90', 'port': 8443})
        for value in ('https://127.0.0.1:8443', 'http://192.168.2.90:8443', 'https://192.168.2.90:8443/path',
                      'https://user:pass@192.168.2.90:8443', 'https://192.168.2.90:443'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                B.origin_config(value)

    def test_terminal_health_cannot_adopt_missing_original_dependency_receipt(self):
        b, h = backend('recover', 'accepted')
        del b.check
        b.k = types.SimpleNamespace(journal_identity=lambda *a: True)
        b.physical = types.SimpleNamespace(check=lambda: True)
        b.dependencies = {'original': True}
        with mock.patch.object(B.os.path, 'lexists', return_value=False):
            with self.assertRaisesRegex(ValueError, 'baseline is missing'):
                b.check()

    def test_recovery_uses_owned_enrolled_entry_without_portable_capture_authority(self):
        raw = B.recovery_unit(ctx())
        self.assertIn(b'--enrolled recover', raw)
        self.assertIn(b'After=tesla-waydroid-runtime.service', raw)
        self.assertNotIn(b'capture-recover', raw)
        self.assertNotIn(b'gpu-default', raw)
        self.assertNotIn('tesla-waydroid-compositor.service', B.OWNED)
        self.assertEqual(len(B.OWNED), 11)


class ActivityTests(unittest.TestCase):
    @staticmethod
    def output(**changes):
        value = {'LoadState': 'not-found', 'ActiveState': 'inactive', 'SubState': 'dead',
                 'MainPID': '0', 'ControlPID': '0', 'Job': ''}
        value.update(changes)
        return ('\n'.join(k + '=' + v for k, v in value.items()) + '\n').encode('ascii')

    def test_real_activity_path_accepts_return4_only_for_exact_unloaded_stopped_state(self):
        b, h = backend()
        del b.activity
        b.p = types.SimpleNamespace(collect=mock.Mock(side_effect=AssertionError('Old nonzero-rejecting collector called')))
        result = types.SimpleNamespace(returncode=4, stdout=self.output(), stderr=b'')
        with mock.patch.object(B.subprocess, 'run', return_value=result) as run:
            value = b.activity(B.VIDEO)
        self.assertEqual(value, {'LoadState': 'not-found', 'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0', 'Job': ''})
        run.assert_called_once_with(['/usr/bin/systemctl', 'show', B.VIDEO, '--all',
            '--property=' + ','.join(B.ACTIVITY_FIELDS)], stdin=B.subprocess.DEVNULL,
            capture_output=True, env=B.ENV, cwd='/', timeout=10)
        # Native .7 reports exactly the same not-found record with exit 0 when
        # LoadState is requested. Both explicit code paths must remain valid.
        result.returncode = 0
        with mock.patch.object(B.subprocess, 'run', return_value=result):
            self.assertEqual(b.activity(B.VIDEO), value)
        result.returncode = 4
        for change in ({'LoadState': 'loaded'}, {'ActiveState': 'failed'}, {'SubState': 'running'},
                       {'MainPID': '42'}, {'ControlPID': '43'}, {'Job': '9/start'}):
            result.stdout = self.output(**change)
            with self.subTest(change=change), mock.patch.object(B.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                b.activity(B.VIDEO)

    def test_actual_stopped_path_accepts_garbage_collected_workers_after_original_stop(self):
        b, h = backend()
        del b.activity
        h.live, h.lease = False, False
        def observe(args, **kwargs):
            unit = args[2]
            self.assertIn(unit, B.OWNED)
            return types.SimpleNamespace(returncode=0 if unit == B.RUNTIME else 4,
                stdout=self.output(LoadState='loaded' if unit == B.RUNTIME else 'not-found'), stderr=b'')
        with mock.patch.object(B.subprocess, 'run', side_effect=observe) as run, \
             mock.patch.object(B.os.path, 'lexists', side_effect=h.exists):
            self.assertTrue(b.stopped())
        self.assertEqual(run.call_count, len(B.OWNED))
        self.assertEqual({call.args[0][2] for call in run.call_args_list}, set(B.OWNED))

    def test_other_exit_codes_incomplete_fields_and_unbounded_output_refuse(self):
        b, h = backend()
        del b.activity
        cases = [(1, self.output(), b''), (3, self.output(), b''), (5, self.output(), b''),
                 (4, self.output().replace(b'MainPID=0\n', b''), b''),
                 (4, self.output() + b'Job=\n', b''), (4, self.output() + b'Foreign=yes\n', b''),
                 (4, b'x' * 8193, b''), (4, self.output(), b'x' * 8193), (4, b'', b'not found')]
        for code, stdout, stderr in cases:
            result = types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
            with self.subTest(code=code, stdout=stdout[:80]), mock.patch.object(B.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                b.activity(B.VIDEO)
        with mock.patch.object(B.subprocess, 'run') as run, self.assertRaises(ValueError):
            b.activity('foreign.service')
        run.assert_not_called()

    def test_loaded_active_service_observes_normally_but_cannot_prove_stopped(self):
        b, h = backend()
        del b.activity
        result = types.SimpleNamespace(returncode=0, stdout=self.output(LoadState='loaded', ActiveState='active', SubState='running', MainPID='42'), stderr=b'')
        with mock.patch.object(B.subprocess, 'run', return_value=result):
            self.assertEqual(b.activity(B.VIDEO)['MainPID'], '42')
            with self.assertRaisesRegex(ValueError, 'fully stopped'):
                b.stopped()



class GatewaySourceTests(unittest.TestCase):
    def setUp(self):
        self.p = load('legacy-gpu-preflight.py')
        self.origin = 'https://192.168.2.90:8443'
        assignments = {'NODE_PATH': self.p.NODE_PATH, 'GATEWAY_ORIGIN': self.origin, 'GATEWAY_BIND': '192.168.240.112',
            'GATEWAY_ANDROID_LOOPBACK': '1', 'GATEWAY_TLS_KEY_FILE': '/home/hilman/.config/teslaandroid-waydroid/key.pem',
            'GATEWAY_TLS_CERT_FILE': '/home/hilman/.config/teslaandroid-waydroid/cert.pem',
            'GATEWAY_DISPLAY_SOCKET': '/run/teslaandroid-waydroid/display.sock', 'GATEWAY_H264_STREAM': '1',
            'GATEWAY_AUDIO_STREAM': '1', 'GATEWAY_VIDEO_SETTINGS': '1', 'GATEWAY_AUDIO_PCM_GAIN': '1',
            'GATEWAY_DEVICE_INFO': '1', 'GATEWAY_REAR_DISPLAY': '1', 'GATEWAY_GPS_SOCKET': '/run/teslaandroid-waydroid/gps.sock'}
        self.parent_argv = (*B.GATEWAY_RUNUSER, *(k + '=' + v for k, v in assignments.items()), *B.GATEWAY_NODE)
        self.cg = ('0::/system.slice/' + B.GATEWAY + '\n').encode()
        self.data = {
            500: {'exe': '/usr/sbin/runuser', 'uid': 0, 'ppid': 1, 'birth': '10', 'cmdline': self.encoded(self.parent_argv),
                  'cgroup': self.cg, 'children': b'501 ', 'environ': b'PARENT_FIELD=ignored\0'},
            501: {'exe': '/usr/bin/node', 'uid': 1000, 'ppid': 500, 'birth': '11', 'cmdline': self.encoded(B.GATEWAY_NODE),
                  'cgroup': self.cg, 'children': b'', 'environ': b'PRIVATE_FIELD=do-not-return\0GATEWAY_ORIGIN=' + self.origin.encode() + b'\0'}}
        self.namespaces = {}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.constructed = []
        test = self
        class Process:
            def __init__(self, stack, pid, executable=Path('/usr/bin/node')):
                row = test.data[pid]
                if executable.as_posix() != row['exe']:
                    raise ValueError('Kernel executable differs')
                self.pid, self.fd, self.birth = pid, pid * 10, row['birth']
                self.targets = {'root': pid * 10 + 1, 'ns/mnt': pid * 10 + 2}
                test.constructed.append((pid, executable.as_posix()))
                self.verify()
            def read(self, name, limit):
                return test.data[self.pid][name]
            def verify(self):
                if test.data[self.pid]['birth'] != self.birth:
                    raise ValueError('Kernel process birth changed')
        def record(ino):
            return types.SimpleNamespace(st_dev=1, st_ino=ino, st_mode=stat.S_IFDIR | 0o755,
                st_uid=0, st_gid=0, st_nlink=1, st_size=1, st_mtime_ns=0, st_ctime_ns=0)
        def path_stat(path, *a, **kw):
            name = str(path).replace('\\', '/')
            if name.startswith('/proc/') and name.endswith('/exe'):
                return record(10 if self.data[int(name.split('/')[2])]['exe'] == '/usr/sbin/runuser' else 20)
            if name == '/usr/sbin/runuser': return record(10)
            if name == '/usr/bin/node': return record(20)
            if name == '/proc/1/root': return record(1)
            if name == '/proc/1/ns/mnt': return record(2)
            raise AssertionError('Unexpected OS pathname')
        def fd_stat(fd):
            return record(self.namespaces.get(fd, fd % 10))
        self.stack.enter_context(mock.patch.object(self.p, 'Process', Process))
        self.stack.enter_context(mock.patch.object(B.os, 'stat', side_effect=path_stat))
        self.stack.enter_context(mock.patch.object(B.os, 'fstat', side_effect=fd_stat))
        self.stack.enter_context(mock.patch.object(B, 'gateway_kernel', side_effect=self.kernel))

    @staticmethod
    def encoded(argv):
        return b'\0'.join(value.encode('ascii') for value in argv) + b'\0'

    def kernel(self, process, name):
        row = self.data[process.pid]
        if name == 'status':
            return ('Name: harmless-fixture\nUid:\t' + '\t'.join([str(row['uid'])] * 4) + '\nPPid:\t' + str(row['ppid']) + '\n').encode()
        if name == 'cgroup': return row['cgroup']
        if name == 'task/' + str(process.pid) + '/children': return row['children']
        raise AssertionError('Unexpected kernel field')

    def source(self, pid=500):
        return B.GatewaySource(self.p, self.stack, pid)

    def test_actual_runuser_parent_selects_sole_original_uid1000_node(self):
        source = self.source()
        self.assertTrue(source.verify())
        self.assertEqual(source.origin, self.origin)
        self.assertEqual(self.constructed, [(500, '/usr/sbin/runuser'), (501, '/usr/bin/node')])
        self.assertFalse(any(k in source.__dict__ for k in ('environ', 'environment', 'raw_environment')))

    def test_direct_node_mainpid_is_supported_with_exact_argv_uid_and_namespace(self):
        self.data[501]['ppid'] = 1
        source = self.source(501)
        self.assertFalse(source.runuser)
        self.assertTrue(source.verify())
        self.assertEqual(source.origin, self.origin)
        self.assertEqual(self.constructed, [(501, '/usr/bin/node')])

    def test_multiple_or_retargeted_children_and_wrong_parent_uid_refuse(self):
        saved = copy.deepcopy(self.data)
        for change in ('siblings', 'no-child', 'parent-uid', 'child-uid', 'child-ppid', 'parent-cgroup', 'child-cgroup', 'child-argv'):
            self.data = copy.deepcopy(saved)
            if change == 'siblings': self.data[500]['children'] = b'501 502 '
            elif change == 'no-child': self.data[500]['children'] = b''
            elif change == 'parent-uid': self.data[500]['uid'] = 1000
            elif change == 'child-uid': self.data[501]['uid'] = 0
            elif change == 'child-ppid': self.data[501]['ppid'] = 99
            elif change == 'parent-cgroup': self.data[500]['cgroup'] = b'0::/foreign\n'
            elif change == 'child-cgroup': self.data[501]['cgroup'] = b'0::/foreign\n'
            else: self.data[501]['cmdline'] = self.encoded(('/usr/bin/node', '/foreign.js'))
            with self.subTest(change=change), self.assertRaises(ValueError): self.source()

    def test_exact_runuser_recipe_rejects_wrong_account_nodepath_and_extra_arguments(self):
        saved = self.data[500]['cmdline']
        variants = [saved.replace(b'hilman\0', b'root\0', 1), saved.replace(self.p.NODE_PATH.encode(), b'/foreign'),
                    saved.replace(b'192.168.240.112', b'127.0.0.1'),
                    self.encoded((*self.parent_argv[:-4], 'NODE_OPTIONS=--require=/foreign.js', *B.GATEWAY_NODE)),
                    self.encoded((*self.parent_argv, '--extra'))]
        for raw in variants:
            self.data[500]['cmdline'] = raw
            with self.assertRaises(ValueError): self.source()
        self.data[500]['cmdline'] = saved

    def test_parent_and_child_birth_argv_namespace_and_origin_are_retained(self):
        saved = copy.deepcopy(self.data)
        for change in ('parent-birth', 'child-birth', 'parent-argv', 'child-argv', 'children', 'child-ppid', 'cgroup', 'namespace', 'origin'):
            self.data, self.namespaces = copy.deepcopy(saved), {}
            source = self.source()
            if change == 'parent-birth': self.data[500]['birth'] = '20'
            elif change == 'child-birth': self.data[501]['birth'] = '21'
            elif change == 'parent-argv': self.data[500]['cmdline'] += b'--foreign\0'
            elif change == 'child-argv': self.data[501]['cmdline'] += b'--foreign\0'
            elif change == 'children': self.data[500]['children'] = b'502 '
            elif change == 'child-ppid': self.data[501]['ppid'] = 1
            elif change == 'cgroup': self.data[501]['cgroup'] = b'0::/foreign\n'
            elif change == 'namespace': self.namespaces[5012] = 99
            else: self.data[501]['environ'] = b'GATEWAY_ORIGIN=https://192.168.2.91:8443\0'
            with self.subTest(change=change), self.assertRaises(ValueError): source.verify()

    def test_final_child_environment_read_cannot_outlive_cgroup_admission(self):
        source = self.source()
        original_read = source.node.read
        def read(name, limit):
            value = original_read(name, limit)
            if name == 'environ': self.data[501]['cgroup'] = b'0::/foreign\n'
            return value
        source.node.read = read
        with self.assertRaises(ValueError): source.verify()

    def test_original_config_returns_only_public_origin_and_rechecks_service(self):
        b, _ = backend()
        b.p = self.p
        fields = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '500', 'ControlPID': '0', 'Job': ''}
        b.r.fields = mock.Mock(side_effect=[dict(fields), dict(fields)])
        self.assertEqual(b.original_config(), {'lanAddress': '192.168.2.90', 'port': 8443})
        b.r.fields = mock.Mock(side_effect=[dict(fields), {**fields, 'MainPID': '502'}])
        with self.assertRaisesRegex(ValueError, 'service generation'): b.original_config()



class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.p = load('legacy-gpu-preflight.py')
        self.fields = {'MainPID': '4242', 'ControlPID': '0', 'ActiveState': 'active', 'SubState': 'running', 'Job': '',
            'FragmentPath': '/run/systemd/transient/' + self.p.UNIT,
            'DropInPaths': L.video_dropin(self.p.UNIT).as_posix(), 'Description': 'TeslaAndroid runtime ' + 'b' * 32,
            'BindsTo': R.RUNTIME, 'BindPaths': '', 'BindReadOnlyPaths': '', 'RootDirectory': '', 'RootImage': '', 'TemporaryFileSystem': ''}
        self.env = {b'TESLA_WAYDROID_RUNTIME_OWNER': b'b' * 32, b'TESLA_WAYDROID_RUNTIME_ROLE': b'video',
                    b'NODE_PATH': self.p.NODE_PATH.encode(), b'LD_LIBRARY_PATH': b'/opt/teslaandroid-waydroid/libgbinder'}
        self.lease = {'version': 1, 'lease': 'b' * 32}

    def validate(self, command=None, executable=Path('/usr/bin/node')):
        return B.validate_video_process(self.p, L, ctx(), 4242, self.fields, self.env, self.lease, self.p.PINS,
                                        self.p.COMMAND if command is None else command, executable)

    def test_exact_original_node_cpu_failsafe_is_accepted_and_reported_cpu(self):
        self.assertEqual(self.validate(), 'cpu')
        for command in (b'/usr/bin/node\0/foreign.js\0', self.p.COMMAND + b'--extra\0', b'/usr/bin/node\0-e\0code\0'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.validate(command)

    def test_python_parent_remains_admitted_without_claiming_gpu_from_argv(self):
        command = b'\0'.join(x.encode() for x in L.parent_command(ctx())) + b'\0'
        self.assertEqual(self.validate(command, Path('/usr/bin/python3')), 'supervised')
        with self.assertRaises(ValueError):
            self.validate(command, Path('/usr/bin/node'))

    def test_full_cpu_fallback_retains_kernel_cgroup_pid1_and_source_guards(self):
        b, h = backend()
        del b.video_check
        h.maps.add('video')
        b.r.fields = lambda unit, names=R.GRAPH: ({'PrivateMounts': 'no', 'KillMode': 'control-group', 'User': 'root', 'Group': 'root'}
            if names != R.GRAPH else {**graph(B.VIDEO), 'DropInPaths': K.VIDEO.as_posix()})
        b.r.sha = R.sha
        b.l, b.p = L, self.p
        source_path = Path('/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-h264.py')
        source_id = (1, 9, stat.S_IFREG | 0o644, 0, 0, 1, 10, 0, 0)
        b.physical = types.SimpleNamespace(held=types.SimpleNamespace(files={source_path: (77, source_id)}))
        b.dependencies = {'files': {n: {'sha256': pin} for n, pin in self.p.PINS.items()}}
        raw_env = b'\0'.join(k + b'=' + v for k, v in self.env.items()) + b'\0'
        process = types.SimpleNamespace(fd=77, targets={'root': 11, 'ns/mnt': 12},
            read=lambda name, limit: self.p.COMMAND if name == 'cmdline' else raw_env,
            source_identity=lambda path: source_id, verify=mock.Mock())
        held = types.SimpleNamespace(read=lambda *a: json.dumps(self.lease).encode(), verify=mock.Mock())
        namespace = [4]
        def record(ino, mode=stat.S_IFDIR | 0o755):
            return types.SimpleNamespace(st_dev=1, st_ino=ino, st_mode=mode, st_uid=0, st_gid=0,
                                         st_nlink=1, st_size=1, st_mtime_ns=0, st_ctime_ns=0)
        def path_stat(path, *args, **kwargs):
            path = str(path).replace('\\', '/')
            if path.endswith('/exe') or path == '/usr/bin/node': return record(20, stat.S_IFREG | 0o755)
            if path == '/usr/bin/python3': return record(10, stat.S_IFREG | 0o755)
            return record(3 if path.endswith('/root') else 4)
        def fd_stat(fd):
            return record(3 if fd == 11 else namespace[0]) if fd in (11, 12) else record(90, stat.S_IFREG | 0o444)
        with mock.patch.object(self.p, 'Process', return_value=process), mock.patch.object(self.p, 'Held', return_value=held), \
             mock.patch.object(self.p, 'unit_fields', side_effect=lambda: copy.deepcopy(self.fields)), \
             mock.patch.object(B.os, 'stat', side_effect=path_stat), mock.patch.object(B.os, 'fstat', side_effect=fd_stat), \
             mock.patch.object(B.os, 'O_NOFOLLOW', getattr(B.os, 'O_NOFOLLOW', 0), create=True), \
             mock.patch.object(B.os, 'O_NONBLOCK', getattr(B.os, 'O_NONBLOCK', 0), create=True), \
             mock.patch.object(B.os, 'open', return_value=91), mock.patch.object(B.os, 'close'), \
             mock.patch.object(B.os, 'read', return_value=L.CGROUP.format(unit=B.VIDEO).encode()) as read:
            self.assertTrue(b.video_check())
            self.assertEqual(b.video_mode, 'cpu')
            namespace[0] = 99
            with self.assertRaisesRegex(ValueError, 'PID1'):
                b.video_check()
            namespace[0] = 4
            read.return_value = b'0::/foreign.service\n'
            with self.assertRaisesRegex(ValueError, 'cgroup'):
                b.video_check()

    def test_cpu_admission_refuses_foreign_lease_opt_in_mapping_and_namespace_graph(self):
        for change in ('lease', 'environment', 'mapping', 'namespace'):
            original = copy.deepcopy((self.lease, self.env, self.fields))
            if change == 'lease':
                self.lease['lease'] = 'c' * 32
            elif change == 'environment':
                self.env[b'DROIDMATE_EXPERIMENTAL_H264_ENCODER'] = b'vaapi'
            elif change == 'mapping':
                self.fields['DropInPaths'] += ' /foreign'
            else:
                self.fields['BindReadOnlyPaths'] = '/private:/original'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate()
            self.lease, self.env, self.fields = original



class IntegrationTests(unittest.TestCase):
    def test_closed_engine_grammar_admits_services_and_video_but_refuses_foreign(self):
        class Boundary(Exception):
            pass
        j = types.SimpleNamespace(guard=lambda: (_ for _ in ()).throw(Boundary()))
        for name in ('services.json', 'map-video.json'):
            with self.subTest(name=name), self.assertRaises(Boundary):
                C.Journal.read(j, name)
            with self.subTest(name=name), self.assertRaises(Boundary):
                C.Journal.write(j, name, {})
        with self.assertRaises(ValueError):
            C.Journal.read(j, 'map-foreign.json')
        j = types.SimpleNamespace(guard=lambda: True, fd=1, intent={'transactionToken': 'b' * 32},
                                 read=lambda name: (_ for _ in ()).throw(Boundary()))
        with mock.patch.object(C.os, 'listdir', return_value=['intent.json', 'services.json', 'map-video.json']):
            with self.assertRaises(Boundary):
                C.Journal.history(j)
        self.assertEqual(C.TARGETS['video'], K.VIDEO)

    def test_material_and_release_include_new_backend_and_guard(self):
        m, a = load('aggregate-material.py'), load('aggregate-release.py')
        for name in ('tools/aggregate-legacy-runtime.py', 'tools/aggregate-legacy-backend.py'):
            self.assertIn(name, m.MODULES)
            self.assertIn(name, a.SOURCES)
        self.assertIn('tools/aggregate_legacy_backend_test.py', a.SOURCES)

    def compiler(self, module):
        def source(context, name):
            if name.endswith('aggregate-legacy-backend.py'):
                return module
            if name.endswith('aggregate-legacy-runtime.py'):
                return types.SimpleNamespace(run=mock.Mock(return_value={'mode': 'stopped'}))
            raise AssertionError('Portable enrollment must never run: ' + name)
        return types.SimpleNamespace(retain=lambda c: c, materialize=lambda c: types.SimpleNamespace(assets={}), source_module=source)

    def test_runtime_dispatches_enrolled_video_without_writing_or_locking_journal(self):
        dispatch = mock.Mock(return_value={'mode': 'stopped'})
        compiler = self.compiler(None)
        compiler.source_module = lambda *a: types.SimpleNamespace(run=dispatch)
        with mock.patch.object(R, 'load_material', return_value=compiler), mock.patch.object(R.sys, 'platform', 'linux'), \
             mock.patch.object(R.os, 'geteuid', return_value=0, create=True), mock.patch.dict(R.os.environ, {}, clear=True), \
             mock.patch.object(R.os.path, 'lexists', side_effect=lambda p: p != R.CONFIG):
            self.assertEqual(R.main(['legacy-video'], ctx(), C), {'mode': 'stopped'})
        dispatch.assert_called_once()

    def test_runtime_recovery_selects_legacy_journal_and_real_backend(self):
        b, h = backend('recover', 'mapped')
        for key in ('gateway', 'video'):
            h.mapping(key).prepare()
            h.mapping(key).publish()
        module = types.SimpleNamespace(recovery_unit=B.recovery_unit, LegacyBackend=mock.Mock(return_value=b))
        compiler = self.compiler(module)
        class Session:
            def __init__(self, root, identity, create=False):
                self.identity = identity
                if identity != {'layout': 'legacy', 'packageSha256': DIGEST} or create:
                    raise AssertionError('Wrong journal identity')
            def __enter__(self):
                return b.j
            def __exit__(self, *args):
                pass
        engine = types.SimpleNamespace(Journal=Session, transact=C.transact)
        def exists(path):
            if path == R.CONFIG:
                return False
            if path in (R.ROOT, R.ROOT / DIGEST, '/opt/teslaandroid-waydroid/runtime'):
                return True
            return h.exists(path)
        with mock.patch.object(R, 'load_material', return_value=compiler), mock.patch.object(R.sys, 'platform', 'linux'), \
             mock.patch.object(R.os, 'geteuid', return_value=0, create=True), mock.patch.dict(R.os.environ, {}, clear=True), \
             mock.patch.object(R.os.path, 'lexists', side_effect=exists):
            self.assertEqual(R.main(['recover'], ctx(), engine)['phase'], 'restored')
        self.assertFalse(h.maps)
        module.LegacyBackend.assert_called_once()

    def test_shared_health_uses_legacy_mapping_and_original_authenticated_routes(self):
        b, h = backend()
        del b.health
        b.r = R
        h.maps.update({'gateway', 'video'})
        actual = {**b.baseline['gateway'], 'DropInPaths': str(C.TARGETS['gateway']), 'PrivateMounts': 'yes',
                  'BindReadOnlyPaths': str(R.PUBLIC / DIGEST / 'legacy/runtime') + ':' + B.LegacyBackend.runtime_target + ':rbind'}
        def fields(unit, names=R.GRAPH):
            return {'ActiveState': 'active'} if names == ('ActiveState',) else actual
        requests = []
        class Response:
            status = 200
            def __init__(self, raw): self.raw = raw
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, limit): return self.raw
        def open_request(request, timeout):
            if isinstance(request, str):
                requests.append((request, 'GET', None))
                return Response(b'page')
            requests.append((request.full_url, request.get_method(), request.get_header('Origin')))
            if request.full_url.endswith('/api/displaySession'):
                return Response(json.dumps({'token': 'd' * 64}).encode())
            if request.full_url.endswith('/api/displayState'):
                return Response(json.dumps({'transactionStatus':'idle','recoveryRequired':False}).encode())
            return Response(json.dumps({'browserAudioSupported': 1, 'persist.tesla-android.browser_audio.is_enabled': 0}).encode())
        class TLS:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def getpeercert(self, binary_form): return b'original public certificate'
        opener = types.SimpleNamespace(open=open_request)
        tls = types.SimpleNamespace(wrap_socket=lambda *a, **kw: TLS())
        with mock.patch.object(R, 'browser_deadline', side_effect=lambda deadline: __import__('contextlib').nullcontext()), \
             mock.patch.object(R, 'fields', side_effect=fields), \
             mock.patch.object(R.urllib.request, 'build_opener', return_value=opener), \
             mock.patch.object(R.ssl, '_create_unverified_context', return_value=tls), \
             mock.patch.object(R.socket, 'create_connection', return_value=TLS()), \
             mock.patch.object(R, 'stream_upgrade', return_value=True) as stream:
            self.assertTrue(b.health())
        self.assertIn(('https://192.168.2.90:8443/api/displaySession', 'POST', 'https://192.168.2.90:8443'), requests)
        self.assertIn(('https://192.168.2.90:8443/api/configuration', 'GET', None), requests)
        self.assertEqual(stream.call_args.args[0:2], ('192.168.2.90', 8443))

    def test_video_stop_during_recovery_does_not_reenter_lost_mapping_guard(self):
        # QS has completed its owned descendant drain before bridge.run returns.
        # No post-return guard can reject recovering/restored/accepted shutdown.
        for phase in ('recovering', 'restored', 'accepted'):
            journal = Journal(phase)
            journal.__enter__ = lambda: journal
            class Session:
                def __enter__(self): return journal
                def __exit__(self, *a): pass
            bridge = types.SimpleNamespace(retain_context=lambda c: c,
                run=lambda *a: {'mode': 'stopped', 'signal': 15})
            checks = []
            class Guard:
                def __init__(self, *a): self.ctx, self.bridge = ctx(), bridge
                def __enter__(self): return self
                def __exit__(self, *a): checks.append('closed-after-drain')
                def check(self): checks.append('admit');return True
            compiler = types.SimpleNamespace(source_module=lambda *a: bridge)
            with mock.patch.object(K.os, 'name', 'posix'), mock.patch.object(K.os, 'geteuid', return_value=0, create=True), \
                 mock.patch.dict(K.os.environ, {}, clear=True), mock.patch.object(K, 'read_journal', return_value=Session()), \
                 mock.patch.object(K, 'Admission', Guard):
                self.assertEqual(K.run(ctx(), C, None, compiler)['mode'], 'stopped')
            self.assertEqual(checks, ['admit', 'closed-after-drain'])

    def test_baseline_establishment_writes_dependencies_then_separate_services_once(self):
        b, h = backend()
        del b.establish
        b.baseline, b.m = None, None
        writes = []
        original_write = b.j.write
        def write(name, value):
            writes.append(name)
            original_write(name, value)
        b.j.write = write
        b.dependencies = {'original': 'physical fixture'}
        def dependencies(*args):
            if 'baseline.json' not in b.j.records:
                b.j.write('baseline.json', b.dependencies)
            return True
        b.k = types.SimpleNamespace(establish=dependencies)
        control_id = (1, 2, stat.S_IFREG | 0o644, 0, 0, 1, 10, 0, 0)
        class Held:
            def __init__(self, *a): self.files = {}
            def read(self, path, limit, mode):
                self.files[path] = (77, control_id)
                return b'original runtime unit'
            def verify(self): pass
        b.p = types.SimpleNamespace(Held=Held)
        b.r.fields = lambda unit: graph(unit)
        b.r.sha = R.sha
        b.original_config = mock.Mock(return_value={'lanAddress': '192.168.2.90', 'port': 8443})
        with mock.patch.object(B.os.path, 'lexists', side_effect=h.exists), \
             mock.patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644)):
            self.assertTrue(b.establish())
            b.j.phase = 'accepted'
            self.assertTrue(b.establish())
        self.assertEqual(writes, ['baseline.json', 'services.json'])
        b.original_config.assert_called_once_with()
        self.assertEqual(b.j.records['services.json']['kind'], 'droidmate-original-legacy-services')
        self.assertNotIn('config', b.j.records['baseline.json'])

    def test_legacy_baselines_precede_supervised_registration(self):
        b, h = backend()
        events = []
        b.establish = lambda: events.append('physical-and-services-baselines') or True
        class Boundary(Exception): pass
        class Supervisor:
            @staticmethod
            def is_worker(): return False
            def __init__(self, *a): pass
            def prepare(self, raw):
                self_raw = B.recovery_unit(ctx())
                if raw != self_raw: raise AssertionError('Wrong recovery domain')
                events.append('supervised-registration')
                raise Boundary()
            def close(self): pass
        supervisor = types.SimpleNamespace(Supervisor=Supervisor, route_install=lambda *a: 'launch')
        module = types.SimpleNamespace(recovery_unit=B.recovery_unit, LegacyBackend=lambda *a: b)
        compiler = self.compiler(module)
        compiler.source_module = lambda context, name: supervisor if name.endswith('supervisor.py') else module
        class Session:
            def __init__(self, root, identity, create=False):
                if identity['layout'] != 'legacy': raise AssertionError('Wrong layout')
            def __enter__(self): return b.j
            def __exit__(self, *a): pass
        with mock.patch.object(R, 'load_material', return_value=compiler), mock.patch.object(R.sys, 'platform', 'linux'), \
             mock.patch.object(R.os, 'geteuid', return_value=0, create=True), mock.patch.dict(R.os.environ, {}, clear=True), \
             mock.patch.object(R.os.path, 'lexists', side_effect=lambda p: p != R.CONFIG):
            with self.assertRaises(Boundary):
                R.main(['install-existing'], ctx(), types.SimpleNamespace(Journal=Session))
        self.assertEqual(events, ['physical-and-services-baselines', 'supervised-registration'])

    def test_legacy_plan_has_no_journal_or_service_effects(self):
        compiler = self.compiler(B)
        journal = mock.Mock(side_effect=AssertionError('Plan wrote a journal'))
        with mock.patch.object(R, 'load_material', return_value=compiler), mock.patch.object(R.sys, 'platform', 'linux'), \
             mock.patch.object(R.os, 'geteuid', return_value=0, create=True), mock.patch.dict(R.os.environ, {}, clear=True), \
             mock.patch.object(R.os.path, 'lexists', side_effect=lambda p: p != R.CONFIG), \
             mock.patch.object(R, 'command', side_effect=AssertionError('Plan invoked a service')):
            result = R.main(['plan'], ctx(), types.SimpleNamespace(Journal=journal))
        self.assertEqual(result['layout'], 'recognized-legacy')
        self.assertFalse(result['servicesChanged'])
        journal.assert_not_called()

    def test_existing_controls_reopen_before_first_backend_check(self):
        events = []
        # This regression protects terminal/recovery constructor reentry: a
        # services receipt must reopen original control FDs before check uses it.
        physical = types.SimpleNamespace(baseline=lambda _: {'files': {}}, check=lambda: True)
        observer = object()
        k = types.SimpleNamespace(journal_identity=lambda *a: True,
            dependency_pins=lambda _: {}, Physical=lambda *a: physical)
        bridge = types.SimpleNamespace(retain_context=lambda c: c, helper=lambda *a: observer)
        compiler = types.SimpleNamespace(source_module=lambda c, n: k if n.endswith('runtime.py') else bridge,
                                         browser_material=lambda *a: {})
        j = Journal('accepted')
        j.records['services.json'] = {'original': True}
        with mock.patch.object(B.os.path, 'lexists', return_value=True), \
             mock.patch.object(B.LegacyBackend, 'validate_services', side_effect=lambda: events.append('schema')), \
             mock.patch.object(B.LegacyBackend, 'hold_controls', side_effect=lambda: events.append('controls')), \
             mock.patch.object(B.LegacyBackend, 'check', side_effect=lambda: events.append('check')):
            with B.LegacyBackend(ctx(), C, j, None, compiler, 'recover', R):
                pass
        self.assertEqual(events, ['schema', 'controls', 'check'])


if __name__ == '__main__':
    unittest.main()
