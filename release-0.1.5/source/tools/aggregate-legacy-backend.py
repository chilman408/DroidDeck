# SPDX-License-Identifier: GPL-3.0-only
"""Preserving legacy aggregate transactions through the original coupled service.

No portable capture enrollment/policy, VM operations, credential access or native
lease fabrication. The original supervisor stops/drains its own native workers;
RB/QS keeps the CPU parent and owns GPU-child cleanup and CPU fallback.
"""
from contextlib import ExitStack
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

RUNTIME = 'tesla-waydroid-runtime.service'
GATEWAY = 'tesla-waydroid-gateway-preview.service'
VIDEO = 'tesla-waydroid-video-preview.service'
# The compositor/session are independent desktop infrastructure. Stop only the
# original supervisor and ALL ten services from its pinned SERVICES inventory.
OWNED = (RUNTIME, *('tesla-waydroid-' + n + '-preview.service' for n in
    ('api', 'display-helper', 'display', 'gps', 'video', 'video-relay', 'audio', 'audio-relay', 'gateway', 'lan-relay')))
NATIVE_LEASE = Path('/run/teslaandroid-waydroid/runtime/native-lease.json')
ACTIVITY = ('ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job')
ACTIVITY_FIELDS = ('LoadState', *ACTIVITY)
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}


def recovery_unit(ctx):
    digest = ctx['release']['packageSha256']
    if not re.fullmatch('[a-f0-9]{64}', digest) or ctx['root'] != Path('/usr/local/share/droidmate-rc18-' + digest):
        raise ValueError('Original enrolled legacy root required')
    return ('[Unit]\nDescription=DroidMate owned legacy installation recovery\n'
        'After=tesla-waydroid-runtime.service\nConditionPathExists=/var/lib/droidmate-aggregate/' + digest + '/intent.json\n'
        '[Service]\nType=oneshot\nUMask=0077\nPrivateMounts=no\nKillMode=control-group\n'
        'ExecStart=/usr/bin/python3 -I -B ' + (ctx['root'] / 'source/tools/aggregate-installer-entry.py').as_posix() +
        ' --enrolled recover\nTimeoutStartSec=360\nRestart=on-failure\nRestartSec=5\n'
        '[Install]\nWantedBy=multi-user.target\n').encode('ascii')


def origin_config(value):
    match = re.fullmatch(r'https://([0-9.]+):8443', value) if type(value) is str else None
    if not match:
        raise ValueError('Original public legacy IPv4 HTTPS origin required')
    host = match[1]
    ip = ipaddress.IPv4Address(host)
    if str(ip) != host or not any(ip in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Original private LAN gateway required')
    return {'lanAddress': host, 'port': 8443}


GATEWAY_NODE = ('/usr/bin/node', '/opt/teslaandroid-waydroid/runtime/preview-gateway/server.js',
                '/home/hilman/teslaandroid-waydroid/frontend', '8443')
GATEWAY_RUNUSER = ('/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env')


def gateway_argv(raw):
    if not raw or len(raw) > 8192 or not raw.endswith(b'\0'):
        raise ValueError('Bounded complete original gateway argv required')
    values = raw[:-1].decode('ascii').split('\0')
    if any(not value for value in values):
        raise ValueError('Unambiguous original gateway argv required')
    return tuple(values)


def gateway_runuser_origin(observer, argv):
    if argv[:5] != GATEWAY_RUNUSER or argv[-4:] != GATEWAY_NODE:
        raise ValueError('Exact original runuser gateway command required')
    assignments = {}
    for value in argv[5:-4]:
        key, sep, item = value.partition('=')
        if not sep or key in assignments:
            raise ValueError('Closed original gateway command environment required')
        assignments[key] = item
    origin = assignments.get('GATEWAY_ORIGIN', '')
    origin_config(origin)
    bind = assignments.get('GATEWAY_BIND', '')
    try:
        address = ipaddress.IPv4Address(bind)
    except ipaddress.AddressValueError as error:
        raise ValueError('Original private container gateway bind required') from error
    if str(address) != bind or address not in ipaddress.IPv4Network('192.168.240.0/24') or not 2 <= address.packed[-1] <= 254:
        raise ValueError('Original private container gateway bind required')
    expected = {'NODE_PATH': observer.NODE_PATH, 'GATEWAY_ORIGIN': origin, 'GATEWAY_BIND': bind,
        'GATEWAY_ANDROID_LOOPBACK': '1', 'GATEWAY_TLS_KEY_FILE': '/home/hilman/.config/teslaandroid-waydroid/key.pem',
        'GATEWAY_TLS_CERT_FILE': '/home/hilman/.config/teslaandroid-waydroid/cert.pem',
        'GATEWAY_DISPLAY_SOCKET': '/run/teslaandroid-waydroid/display.sock', 'GATEWAY_H264_STREAM': '1',
        'GATEWAY_AUDIO_STREAM': '1', 'GATEWAY_VIDEO_SETTINGS': '1', 'GATEWAY_AUDIO_PCM_GAIN': '1',
        'GATEWAY_DEVICE_INFO': '1', 'GATEWAY_REAR_DISPLAY': '1', 'GATEWAY_GPS_SOCKET': '/run/teslaandroid-waydroid/gps.sock'}
    if assignments != expected:
        raise ValueError('Exact original gateway command environment required')
    return origin


def gateway_kernel(process, name):
    if name not in ('status', 'cgroup', 'task/' + str(process.pid) + '/children'):
        raise ValueError('Fixed gateway kernel observation required')
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=process.fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Original gateway kernel record required')
        raw = bytearray()
        while len(raw) <= 65536:
            part = os.read(fd, min(4096, 65537 - len(raw)))
            if not part:
                break
            raw.extend(part)
        if len(raw) > 65536:
            raise ValueError('Bounded gateway kernel observation required')
        return bytes(raw)
    finally:
        os.close(fd)


def gateway_identity(observer, process, uid):
    fields = {}
    for line in gateway_kernel(process, 'status').splitlines():
        name, sep, value = line.partition(b':')
        if name in (b'Uid', b'PPid'):
            if not sep or name in fields:
                raise ValueError('Unambiguous gateway kernel credentials required')
            fields[name] = value.split()
    if (set(fields) != {b'Uid', b'PPid'} or fields[b'Uid'] != [str(uid).encode()] * 4
            or len(fields[b'PPid']) != 1 or not re.fullmatch(rb'[1-9][0-9]*', fields[b'PPid'][0])):
        raise ValueError('Original gateway process UID and parent required')
    expected = ('0::/system.slice/' + GATEWAY + '\n').encode('ascii')
    if gateway_kernel(process, 'cgroup') != expected:
        raise ValueError('Original gateway service cgroup required')
    for name in ('root', 'ns/mnt'):
        if observer.identity(os.fstat(process.targets[name]))[:5] != observer.identity(os.stat('/proc/1/' + name))[:5]:
            raise ValueError('Original gateway PID1 root/mount namespace required')
    return int(fields[b'PPid'][0])


class GatewaySource:
    """Hold the real MainPID and, for runuser, its sole original Node child."""
    def __init__(self, observer, stack, pid):
        self.p, self.pid = observer, pid
        current = os.stat('/proc/' + str(pid) + '/exe')
        executable = None
        for candidate in (Path('/usr/sbin/runuser'), Path('/usr/bin/node')):
            source = candidate.stat()
            if (source.st_dev, source.st_ino) == (current.st_dev, current.st_ino):
                executable = candidate
                break
        if executable is None:
            raise ValueError('Original runuser or direct Node gateway MainPID required')
        self.main = observer.Process(stack, pid, executable=executable)
        self.main_command = self.main.read('cmdline', 8192)
        argv = gateway_argv(self.main_command)
        self.runuser = executable == Path('/usr/sbin/runuser')
        self.main_parent = gateway_identity(observer, self.main, 0 if self.runuser else 1000)
        if self.runuser:
            command_origin = gateway_runuser_origin(observer, argv)
            self.child_pid = self.sole_child()
            self.node = observer.Process(stack, self.child_pid, executable=Path('/usr/bin/node'))
            if gateway_identity(observer, self.node, 1000) != pid:
                raise ValueError('Original gateway Node must belong to retained runuser parent')
        else:
            if argv != GATEWAY_NODE:
                raise ValueError('Exact original direct Node gateway argv required')
            self.node, self.child_pid, command_origin = self.main, None, None
        self.node_command = self.node.read('cmdline', 8192)
        if gateway_argv(self.node_command) != GATEWAY_NODE:
            raise ValueError('Exact original gateway Node argv required')
        self.origin = self.public_origin()
        if command_origin is not None and self.origin != command_origin:
            raise ValueError('Original gateway child public origin differs from parent argv')
        self.verify()

    def sole_child(self):
        raw = gateway_kernel(self.main, 'task/' + str(self.pid) + '/children')
        children = raw.split()
        if len(children) != 1 or not re.fullmatch(rb'[1-9][0-9]*', children[0]):
            raise ValueError('Sole original gateway Node child required')
        child = int(children[0])
        if child < 2 or child == self.pid:
            raise ValueError('Distinct original gateway child PID required')
        return child

    def public_origin(self):
        # Inspect ONLY the requested public field. No environment/credential
        # copy is retained, returned, logged, or stored in services.json.
        raw = self.node.read('environ', 131072)
        if not raw.endswith(b'\0'):
            raise ValueError('Complete gateway process environment required')
        prefix = b'GATEWAY_ORIGIN='
        origins = [value[len(prefix):] for value in raw[:-1].split(b'\0') if value.startswith(prefix)]
        if len(origins) != 1:
            raise ValueError('Sole public gateway origin required')
        origin = origins[0].decode('ascii')
        origin_config(origin)
        return origin

    def verify(self):
        self.main.verify()
        if (self.main.read('cmdline', 8192) != self.main_command
                or gateway_identity(self.p, self.main, 0 if self.runuser else 1000) != self.main_parent):
            raise ValueError('Retained gateway MainPID argv/credentials changed')
        if self.runuser:
            if self.sole_child() != self.child_pid or gateway_identity(self.p, self.node, 1000) != self.pid:
                raise ValueError('Original gateway child selection changed')
        self.node.verify()
        if self.node.read('cmdline', 8192) != self.node_command or self.public_origin() != self.origin:
            raise ValueError('Original gateway Node generation or public origin changed')
        # Recheck parent/child kernel lifetimes after the final child read.
        self.main.verify()
        self.node.verify()
        if (gateway_identity(self.p, self.main, 0 if self.runuser else 1000) != self.main_parent
                or self.runuser and (self.sole_child() != self.child_pid or gateway_identity(self.p, self.node, 1000) != self.pid)
                or self.main.read('cmdline', 8192) != self.main_command
                or self.node.read('cmdline', 8192) != self.node_command):
            raise ValueError('Gateway kernel binding changed after final public origin read')
        self.main.verify()
        self.node.verify()
        return True


def validate_video_process(observer, bridge, context, pid, fields, environment, lease, sources, command, executable):
    if executable == Path('/usr/bin/python3'):
        bridge.validate_parent(observer, context, pid, fields, environment, lease, sources, command)
        # QS may be running either its private GPU candidate or its original CPU
        # fallback child. The parent argv alone is NOT proof of effective GPU.
        return 'supervised'
    if (executable != Path('/usr/bin/node') or fields.get('MainPID') != str(pid)
            or fields.get('DropInPaths') != bridge.video_dropin(observer.UNIT).as_posix()):
        raise ValueError('Exact original CPU process plus sole owned override required')
    bridge.check_context(context)
    observer.validate(sources, {**fields, 'DropInPaths': ''}, command, environment, lease)
    bridge.worker_environments(observer, environment)
    bridge.check_context(context)
    return 'cpu'


class LegacyBackend:
    runtime_view = 'legacy/runtime'
    runtime_target = '/opt/teslaandroid-waydroid/runtime'

    def __init__(self, ctx, engine, journal, material, compiler, action, runtime):
        self.k = compiler.source_module(ctx, 'tools/aggregate-legacy-runtime.py')
        self.l = compiler.source_module(ctx, 'tools/aggregate-legacy-video.py')
        self.ctx = self.l.retain_context(ctx)
        self.c, self.j, self.material, self.m, self.r, self.action = engine, journal, material, compiler, runtime, action
        self.rollback_mode, self.closed = action != 'install', False
        self.stack, self.maps = ExitStack(), {}
        try:
            self.p = self.l.helper(self.ctx, 'legacy-gpu-preflight.py')
            self.k.journal_identity(self.ctx, engine, journal)
            self.physical = self.k.Physical(self.p, self.k.dependency_pins(material), self.stack)
            self.dependencies = self.physical.baseline(self.ctx['release']['packageSha256'])
            self.view = compiler.browser_material(self.ctx, 'legacy')
            self.baseline = journal.read('services.json') if os.path.lexists(journal.root / 'services.json') else None
            if self.baseline is not None:
                self.validate_services()
                self.hold_controls()
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.closed or self.ctx['check']() is not True:
            raise ValueError('Original legacy source admission lost')
        self.k.journal_identity(self.ctx, self.c, self.j)
        self.physical.check()
        has_dependencies = os.path.lexists(self.j.root / 'baseline.json')
        if (self.baseline is not None or self.j.state() != 'new') and not has_dependencies:
            raise ValueError('Original legacy physical dependency baseline is missing')
        if has_dependencies and self.j.read('baseline.json') != self.dependencies:
            raise ValueError('Original legacy physical dependency baseline changed')
        if self.baseline is not None:
            if self.j.read('services.json') != self.baseline:
                raise ValueError('Original immutable legacy service metadata changed')
            self.control_check()
        if self.ctx['check']() is not True:
            raise ValueError('Source changed after legacy dependency/control verification')
        return True

    def validate_services(self):
        value = self.baseline
        if (type(value) is not dict or set(value) != {'schema', 'kind', 'layout', 'packageSha256', 'runtime', 'gateway', 'video', 'config', 'physical'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['kind'] != 'droidmate-original-legacy-services' or value['layout'] != 'legacy'
                or value['packageSha256'] != self.ctx['release']['packageSha256']
                or value['config'] != origin_config('https://' + value['config'].get('lanAddress', '') + ':8443')
                or type(value['physical']) is not dict):
            raise ValueError('Closed original legacy services metadata required')
        for key in ('runtime', 'gateway', 'video'):
            graph = value[key]
            if type(graph) is not dict or set(graph) != set(self.r.GRAPH):
                raise ValueError('Closed original legacy service graph required')
            unit = {'runtime': RUNTIME, 'gateway': GATEWAY, 'video': VIDEO}[key]
            fragment = ('/etc/systemd/system/' if key == 'runtime' else '/run/systemd/transient/') + unit
            if (graph['FragmentPath'] != fragment or graph['PrivateMounts'] != 'no'
                    or any(graph[n] for n in ('BindPaths', 'BindReadOnlyPaths', 'RootDirectory', 'RootImage', 'TemporaryFileSystem'))
                    or key != 'runtime' and graph['DropInPaths']):
                raise ValueError('Original unmapped legacy service layout required')
        expected = {value['runtime']['FragmentPath'], *value['runtime']['DropInPaths'].split()}
        if set(value['physical']) != expected:
            raise ValueError('Complete original persistent runtime control inventory required')
        for name, row in value['physical'].items():
            path = PurePosixPath(name)
            if (not path.is_absolute() or path.as_posix() != name or '..' in path.parts
                    or not name.startswith('/etc/systemd/system/') or type(row) is not dict
                    or set(row) != {'generation', 'sha256'} or type(row['generation']) is not list
                    or len(row['generation']) != 9 or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                raise ValueError('Original protected persistent control record required')

    def control_check(self):
        self.controls.verify()
        if self.r.fields(RUNTIME) != self.baseline['runtime']:
            raise ValueError('Original coupled runtime graph changed')
        return True

    def hold_controls(self):
        self.controls = self.p.Held(self.stack)
        for name, row in self.baseline['physical'].items():
            path = Path(name)
            mode = stat.S_IMODE(path.lstat().st_mode)
            if mode not in (0o444, 0o644):
                raise ValueError('Protected original runtime unit mode required')
            raw = self.controls.read(path, 65536, mode)
            if (self.r.sha(raw) != row['sha256'] or list(self.controls.files[path][1]) != row['generation']):
                raise ValueError('Original persistent runtime unit generation changed')
        self.controls.verify()

    def original_config(self):
        # Read only the public origin from the authenticated original gateway
        # process. Never persist/log environment, access credentials or TLS data.
        before = self.r.fields(GATEWAY, ACTIVITY)
        if (before['ActiveState'] != 'active' or before['SubState'] != 'running' or before['ControlPID'] != '0'
                or before['Job'] or not re.fullmatch('[1-9][0-9]*', before['MainPID'])):
            raise ValueError('Healthy original gateway process required')
        with ExitStack() as stack:
            source = GatewaySource(self.p, stack, int(before['MainPID']))
            source.verify()
            if self.r.fields(GATEWAY, ACTIVITY) != before:
                raise ValueError('Original public gateway service generation changed')
            if self.ctx['check']() is not True:
                raise ValueError('Original source lost during gateway observation')
            source.verify()
            return origin_config(source.origin)

    def establish(self):
        self.k.establish(self.ctx, self.c, self.j, self.material, self.m)
        if self.baseline is None:
            if self.j.state() != 'new':
                raise ValueError('Lost service metadata cannot be recreated after intent')
            runtime, gateway, video = (self.r.fields(u) for u in (RUNTIME, GATEWAY, VIDEO))
            physical = {}
            held = self.p.Held(self.stack)
            for name in {runtime['FragmentPath'], *runtime['DropInPaths'].split()}:
                path = Path(name)
                mode = stat.S_IMODE(path.lstat().st_mode)
                if mode not in (0o444, 0o644):
                    raise ValueError('Original persistent unit mode required')
                raw = held.read(path, 65536, mode)
                physical[name] = {'generation': list(held.files[path][1]), 'sha256': self.r.sha(raw)}
            value = {'schema': 1, 'kind': 'droidmate-original-legacy-services', 'layout': 'legacy',
                     'packageSha256': self.ctx['release']['packageSha256'], 'runtime': runtime,
                     'gateway': gateway, 'video': video, 'config': self.original_config(), 'physical': physical}
            self.baseline = value
            self.validate_services()
            held.verify()
            self.ctx['check']()
            self.j.write('services.json', value)
        if not hasattr(self, 'controls'):
            self.hold_controls()
        self.check()
        return True

    def admit(self):
        self.establish()
        if self.action == 'install':
            self.health(original=True)
        return self.check()

    def map(self, key):
        if key not in self.maps:
            if key == 'video':
                self.maps[key] = self.k.video_mapping(self.ctx, self.c, self.j, self.l)
            else:
                raw = {'gateway': self.view['mapping'], 'recovery': recovery_unit(self.ctx)}.get(key)
                if key == 'installer':
                    raw = self.m.source_module(self.ctx, 'tools/aggregate-supervisor.py').installer_unit(self.ctx)
                self.maps[key] = self.c.OwnedMapping(self.j, key, raw)
        return self.maps[key]

    def directory(self, path):
        if path==self.r.PUBLIC:return self.c.public_parent(self.j,path)
        if not os.path.lexists(path):
            parent = self.j.directory(path.parent)
            os.mkdir(path.name, 0o755, dir_fd=parent)
            os.fsync(parent)
        self.j.directory(path)

    def arm_recovery(self):
        self.check()
        for key in ('gateway', 'video'):
            self.directory(self.c.TARGETS[key].parent)
        mapping = self.map('recovery')
        mapping.prepare()
        mapping.publish()
        link = self.c.OwnedBootLink(self.j)
        link.prepare()
        link.publish()
        return self.check()

    def restore_cpu(self):
        # There is no portable capture controller to call on a legacy host.
        # Graceful whole-runtime stop lets QS drain the child before a mapping
        # is removed, even after recovering was durably appended.
        if self.rollback_mode:
            return self.stop()
        return self.check()

    def activity(self, unit):
        if unit not in OWNED:
            raise ValueError('Closed original coupled runtime unit required')
        # A stopped transient service may already have been garbage-collected.
        # systemctl then returns 4 with a structured not-found/dead observation;
        # that exact state is a successful drain proof, not a generic error waiver.
        result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--all',
                                 '--property=' + ','.join(ACTIVITY_FIELDS)],
            stdin=subprocess.DEVNULL, capture_output=True, env=ENV, cwd='/', timeout=10)
        if (result.returncode not in (0, 4) or len(result.stdout) > 8192 or len(result.stderr) > 8192):
            raise ValueError('Bounded fixed legacy activity observation refused')
        value = {}
        for line in result.stdout.decode('ascii').splitlines():
            key, sep, item = line.partition('=')
            if not sep or key not in ACTIVITY_FIELDS or key in value:
                raise ValueError('Closed original runtime activity fields required')
            value[key] = item
        if set(value) != set(ACTIVITY_FIELDS):
            raise ValueError('Complete original runtime activity required')
        missing = {'LoadState': 'not-found', 'ActiveState': 'inactive', 'SubState': 'dead',
                   'MainPID': '0', 'ControlPID': '0', 'Job': ''}
        if (value['LoadState'] not in ('loaded', 'not-found')
                or (result.returncode == 4 or value['LoadState'] == 'not-found') and value != missing):
            raise ValueError('Only exact unloaded inactive legacy service may return not-found')
        return value

    def stopped(self):
        for unit in OWNED:
            state = self.activity(unit)
            if (state['LoadState'] not in ('loaded', 'not-found') or state['ActiveState'] != 'inactive' or state['MainPID'] != '0' or state['ControlPID'] != '0'
                    or state['Job'] or state['SubState'] != 'dead'):
                raise ValueError('Original coupled runtime has not fully stopped')
        if os.path.lexists(NATIVE_LEASE):
            raise ValueError('Original native lease was not released; preserve mappings')
        return self.check()

    def stop(self):
        self.check()
        self.r.command(['/usr/bin/systemctl', 'stop', RUNTIME], 180)
        # The original supervisor can return exit1 after its signal-driven
        # cleanup. Clear ONLY that failed status, AFTER every worker is gone.
        states={unit:self.activity(unit) for unit in OWNED}
        expected={'LoadState':'loaded','ActiveState':'failed','SubState':'failed','MainPID':'0','ControlPID':'0','Job':''}
        if states[RUNTIME]==expected:
            for unit,state in states.items():
                if unit==RUNTIME:continue
                if (state['LoadState'] not in ('loaded','not-found') or state['ActiveState']!='inactive'
                        or state['SubState']!='dead' or state['MainPID']!='0' or state['ControlPID']!='0' or state['Job']):
                    raise ValueError('Original coupled runtime has not fully stopped')
            if os.path.lexists(NATIVE_LEASE):raise ValueError('Original native lease was not released; preserve mappings')
            self.check();self.r.command(['/usr/bin/systemctl','reset-failed',RUNTIME]);self.check()
        return self.stopped()

    def public_check(self):
        for digest, files in ((self.view['runtimeDigest'], self.view['runtimeFiles']),
                              (self.view['frontendDigest'], self.view['frontendFiles'])):
            self.c.stage_public_tree(self.r.PUBLIC, digest, files, self.check, create=False)
        return self.check()

    def publish(self):
        self.stopped()
        self.directory(self.r.PUBLIC)
        for digest, files in ((self.view['runtimeDigest'], self.view['runtimeFiles']),
                              (self.view['frontendDigest'], self.view['frontendFiles'])):
            self.c.stage_public_tree(self.r.PUBLIC, digest, files, self.check,
                                     create=not os.path.lexists(self.r.PUBLIC / digest))
        for key in ('gateway', 'video'):
            mapping = self.map(key)
            mapping.prepare()
            mapping.publish()
        return self.stopped()

    def rollback(self):
        self.rollback_mode = True
        self.stopped()
        for key in ('video', 'gateway'):
            if os.path.lexists(self.j.root / ('map-' + key + '.json')):
                self.map(key).remove()
            elif os.path.lexists(self.c.TARGETS[key]):
                raise ValueError('Foreign legacy mapping preserved')
        return self.stopped()

    def reload(self):
        self.check()
        self.r.command(['/usr/bin/systemctl', 'daemon-reload'])
        return self.check()

    def start(self):
        self.stopped()
        self.r.command(['/usr/bin/systemctl', 'start', RUNTIME], 180)
        return self.check()

    def video_check(self, original=False):
        graph = self.r.fields(VIDEO)
        expected = self.baseline['video']
        if not original:
            expected = {**expected, 'DropInPaths': self.c.TARGETS['video'].as_posix()}
            if self.map('video').verify() is not True:
                raise ValueError('Owned legacy video map required')
        if graph != expected:
            raise ValueError('Original physical CPU video graph plus sole owned argv required')
        if not original:
            security = self.r.fields(VIDEO, ('PrivateMounts', 'KillMode', 'User', 'Group'))
            if (security['PrivateMounts'] != 'no' or security['KillMode'] != 'control-group'
                    or security['User'] not in ('', 'root') or security['Group'] not in ('', 'root')):
                raise ValueError('Original root video parent control-group supervision required')
            before = self.p.unit_fields()
            pid = int(before['MainPID'])
            current = os.stat('/proc/' + str(pid) + '/exe')
            executable = None
            for candidate in (Path('/usr/bin/python3'), Path('/usr/bin/node')):
                expected_exe = candidate.stat()
                if (current.st_dev, current.st_ino) == (expected_exe.st_dev, expected_exe.st_ino):
                    executable = candidate
                    break
            if executable is None:
                raise ValueError('Original Python supervisor or exact CPU Node executable required')
            with ExitStack() as stack:
                process = self.p.Process(stack, pid, executable=executable)
                for name in ('root', 'ns/mnt'):
                    if self.p.identity(os.fstat(process.targets[name]))[:5] != self.p.identity(os.stat('/proc/1/' + name))[:5]:
                        raise ValueError('Video CPU parent must retain PID1 root/mount namespace')
                held = self.p.Held(stack)
                lease = self.p.lease_document(held.read(NATIVE_LEASE, 512, 0o600))
                mode = validate_video_process(self.p, self.l, self.ctx, pid, before,
                    self.p.environment(process.read('environ', 131072)), lease,
                    {n: self.dependencies['files'][n]['sha256'] for n in self.p.PINS},
                    process.read('cmdline', 4096), executable)
                fd = os.open('cgroup', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=process.fd)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError('Original kernel video cgroup record required')
                    if os.read(fd, 4097) != self.l.CGROUP.format(unit=VIDEO).encode('ascii'):
                        raise ValueError('Actual original video unit cgroup required')
                finally:
                    os.close(fd)
                for path, (_, identity) in self.physical.held.files.items():
                    if process.source_identity(path) != identity:
                        raise ValueError('Video parent CPU dependency namespace differs')
                process.verify()
                held.verify()
                if self.p.unit_fields() != before:
                    raise ValueError('Actual video parent generation changed')
                self.video_mode = mode
        else:
            self.video_mode = 'cpu'
        return self.check()

    def health(self, original=False):
        # Wait for all peers in the original health code BEFORE observing the
        # video parent: systemctl start may return while its peers are starting.
        # Uses the exact existing TLS/session/media code and original Host/Origin.
        self.r.PortableBackend.health(self, original=original)
        self.video_check(original)
        return self.check()

    def verify(self):
        return self.health(original=self.rollback_mode)

    def prefer_gpu(self):
        # GPU is already preferred in the new video parent; QS alone owns its
        # private child and CPU fallback. No second trial, lease or GPU policy.
        self.video_check(original=False)
        return self.check()

    def disarm_recovery(self):
        return self.check()

    def finish(self):
        self.check()
        phase = self.j.state()
        if phase not in ('accepted', 'restored'):
            raise ValueError('Durable terminal legacy state required before cleanup')
        self.health(original=phase == 'restored')
        if phase == 'restored':
            for key in ('installer', 'recovery-link', 'recovery'):
                name = 'map-' + key + '.json'
                if os.path.lexists(self.j.root / name):
                    mapping = self.c.OwnedBootLink(self.j) if key == 'recovery-link' else self.map(key)
                    mapping.remove()
            self.reload()
        return self.check()

    def restore_unstarted(self):
        self.admit()
        if self.j.state() != 'new':
            raise ValueError('Only an unstarted legacy operation may retire without runtime effects')
        for key in ('video', 'gateway'):
            if os.path.lexists(self.j.root / ('map-' + key + '.json')) or os.path.lexists(self.c.TARGETS[key]):
                raise ValueError('Unexpected pre-intent mapping preserved')
        self.health(original=True)
        self.j.append('recovering')
        self.j.append('restored')
        self.finish()
        return {'phase': 'restored', 'servicesChanged': False}

    def close(self):
        if not self.closed:
            self.closed = True
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
