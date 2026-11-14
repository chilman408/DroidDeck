#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Observe a recognized original legacy baseline. Never staging/activation authority."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path('/opt/teslaandroid-waydroid/runtime')
LEASE = Path('/run/teslaandroid-waydroid/runtime/native-lease.json')
UNIT = 'tesla-waydroid-video-preview.service'
PINS = {
    'preview-gateway/compositor-video-launcher.js': '0b1d6f08c0ac6afbe85781c23238fbd57db4dc0ff70230cf41ff132c211edc86',
    'preview-gateway/compositor-h264.py': 'd831a9aa9a34a66820aef35473c2f6e0b357df3719a940a0d2ae16ab966523ad',
}
COMMAND = b'/usr/bin/node\0/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-video-launcher.js\0'
NODE_PATH = str(ROOT / 'preview-gateway/node_modules') + ':' + str(ROOT / 'waydroid-display/node_modules')
FIELDS = ('MainPID', 'ControlPID', 'ActiveState', 'SubState', 'Job', 'FragmentPath', 'DropInPaths',
          'Description', 'BindsTo', 'BindPaths', 'BindReadOnlyPaths', 'RootDirectory', 'RootImage', 'TemporaryFileSystem')
FORBIDDEN = (b'DROIDDECK_PORTABLE_RUNTIME', b'DROIDDECK_CONFIGURATION_SHA256', b'NODE_OPTIONS', b'LD_PRELOAD',
             b'DROIDMATE_EXPERIMENTAL_H264_ENCODER', b'DROIDMATE_GPU_RESIZE', b'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE',
             b'GLIBC_TUNABLES', b'GCONV_PATH', b'OPENSSL_CONF', b'OPENSSL_MODULES', b'PYTHONPATH', b'PYTHONHOME',
             b'PYTHONSTARTUP', b'PYTHONINSPECT', b'GI_TYPELIB_PATH', b'GIO_EXTRA_MODULES', b'GIO_MODULE_DIR')


def validate(sources, fields, command, env, lease):
    if type(sources) is not dict or sources != PINS:
        raise ValueError('Recognized original legacy capture sources required')
    if (type(lease) is not dict or set(lease) != {'version', 'lease'}
            or type(lease['version']) is not int or lease['version'] != 1
            or type(lease['lease']) is not str or not re.fullmatch('[a-f0-9]{32}', lease['lease'])):
        raise ValueError('Original native lease schema required')
    if type(fields) is not dict or set(fields) != set(FIELDS):
        raise ValueError('Closed original video unit observation required')
    expected = {'ActiveState': 'active', 'SubState': 'running', 'ControlPID': '0', 'Job': '',
                'FragmentPath': '/run/systemd/transient/' + UNIT, 'BindsTo': 'tesla-waydroid-runtime.service',
                'Description': 'TeslaAndroid runtime ' + lease['lease'],
                **{k: '' for k in ('DropInPaths', 'BindPaths', 'BindReadOnlyPaths', 'RootDirectory', 'RootImage', 'TemporaryFileSystem')}}
    if (any(fields[k] != value for k, value in expected.items())
            or type(fields['MainPID']) is not str or not re.fullmatch('[1-9][0-9]{1,9}', fields['MainPID'])
            or int(fields['MainPID']) < 2 or command != COMMAND):
        raise ValueError('Original healthy legacy video process/unit required')
    if (type(env) is not dict or any(k in env for k in FORBIDDEN)
            or any(k.startswith(b'LD_') and k != b'LD_LIBRARY_PATH' or k.startswith(b'GST_PLUGIN_') for k in env)
            or env.get(b'TESLA_WAYDROID_RUNTIME_OWNER') != lease['lease'].encode('ascii')
            or env.get(b'TESLA_WAYDROID_RUNTIME_ROLE') != b'video'
            or env.get(b'NODE_PATH') != NODE_PATH.encode('ascii')
            or env.get(b'LD_LIBRARY_PATH') != b'/opt/teslaandroid-waydroid/libgbinder'):
        raise ValueError('Original native owner/role and source environment required')
    return {'schema': 1, 'kind': 'droidmate-legacy-baseline-observation', 'legacyBaselineRecognized': True,
            'sourceSha256': dict(PINS), 'nativeLeaseMatched': True, 'activationAllowed': False,
            'gpuAdmissionAllowed': False, 'installable': False, 'writes': False, 'servicesChanged': False}


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class Held:
    def __init__(self, stack):
        self.stack, self.dirs, self.files = stack, {}, {}

    def directory(self, path):
        path = Path(path)
        if path in self.dirs:
            return self.dirs[path][0]
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('Canonical protected directory required')
        parent = None if path == Path('/') else self.directory(path.parent)
        fd = os.open('/' if parent is None else path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        self.stack.callback(os.close, fd)
        s = os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Root-owned protected ancestry required')
        self.dirs[path] = fd, identity(s)[:5]
        return fd

    def read(self, path, limit, mode):
        path = Path(path)
        if path in self.files:
            self.verify()
            return os.pread(self.files[path][0], limit + 1, 0)
        parent = self.directory(path.parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        self.stack.callback(os.close, fd)
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or (s.st_uid, s.st_gid, s.st_nlink, stat.S_IMODE(s.st_mode)) != (0, 0, 1, mode)
                or not 0 < s.st_size <= limit):
            raise ValueError('Bounded original single-link root-owned file required')
        raw = os.pread(fd, limit + 1, 0)
        if len(raw) != s.st_size:
            raise ValueError('Source changed while reading')
        self.files[path] = fd, identity(s)
        self.verify()
        return raw

    def verify(self):
        for path, (fd, expected) in self.dirs.items():
            if identity(os.fstat(fd))[:5] != expected or identity(path.lstat())[:5] != expected:
                raise ValueError('Original directory generation changed')
        for path, (fd, expected) in self.files.items():
            if identity(os.fstat(fd)) != expected or identity(path.lstat()) != expected:
                raise ValueError('Original file generation changed')


def process_birth(raw, pid):
    if not raw.startswith(str(pid).encode('ascii') + b' (') or b') ' not in raw:
        raise ValueError('Original process stat required')
    fields = raw[raw.rfind(b') ') + 2:].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError('Original process birth required')
    return fields[19].decode('ascii')


def environment(raw):
    if len(raw) > 131072 or not raw.endswith(b'\0'):
        raise ValueError('Bounded complete process environment required')
    result = {}
    for row in raw[:-1].split(b'\0'):
        key, separator, value = row.partition(b'=')
        if not separator or not key or key in result:
            raise ValueError('Unambiguous process environment required')
        result[key] = value
    return result


def collect(args):
    result = subprocess.run(['/usr/bin/systemctl', *args], capture_output=True, stdin=subprocess.DEVNULL,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, timeout=10)
    if result.returncode or len(result.stdout) > 8192 or len(result.stderr) > 8192:
        raise ValueError('Fixed bounded systemd observation failed')
    return result.stdout.decode('ascii')


def unit_fields():
    rows = collect(['show', UNIT, '--property=' + ','.join(FIELDS)]).splitlines()
    value = {}
    for row in rows:
        key, separator, item = row.partition('=')
        if not separator or key in value:
            raise ValueError('Unambiguous unit properties required')
        value[key] = item
    return value


def proc_read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Original kernel process file required')
        raw = b''
        while len(raw) <= limit:
            chunk = os.read(fd, min(4096, limit + 1 - len(raw)))
            if not chunk: break
            raw += chunk
        if not raw or len(raw) > limit:
            raise ValueError('Bounded kernel process observation required')
        return raw
    finally:
        os.close(fd)


class Process:
    """Hold one kernel process directory and original host/target identities."""
    def __init__(self, stack, pid, executable=Path('/usr/bin/node')):
        self.stack, self.pid, self.path = stack, pid, Path('/proc') / str(pid)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, self.fd)
        self.directory_identity = identity(os.fstat(self.fd))[:5]
        self.birth = process_birth(self.read('stat', 8192), pid)
        self.hosts, self.targets = {}, {}
        for name, host in (('root', Path('/')), ('ns/mnt', Path('/proc/self/ns/mnt')), ('exe', executable)):
            # These are fixed kernel magic links. Follow their target, retain
            # its FD, and compare inodes rather than resolved pathnames.
            host_fd = os.open(host, os.O_PATH)
            stack.callback(os.close, host_fd)
            expected = identity(os.fstat(host_fd))
            if name != 'exe': expected = expected[:5]
            else:
                s = os.fstat(host_fd)
                if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
                    raise ValueError('Original protected executable required')
            target_fd = os.open(name, os.O_PATH, dir_fd=self.fd)
            stack.callback(os.close, target_fd)
            observed = identity(os.fstat(target_fd))
            if name != 'exe': observed = observed[:5]
            if observed != expected:
                raise ValueError('Original process namespace/root/executable required')
            self.hosts[name] = host, host_fd, expected
            self.targets[name] = target_fd
        self.verify()

    def read(self, name, limit):
        if name not in ('stat', 'cmdline', 'environ'):
            raise ValueError('Fixed kernel process observation required')
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Original kernel process file required')
            raw = b''
            while len(raw) <= limit:
                chunk = os.read(fd, min(4096, limit + 1 - len(raw)))
                if not chunk: break
                raw += chunk
            if not raw or len(raw) > limit:
                raise ValueError('Bounded original process observation required')
            return raw
        finally:
            os.close(fd)

    def source_identity(self, path):
        fd = os.open(str(path.relative_to('/')), os.O_PATH | os.O_NOFOLLOW, dir_fd=self.targets['root'])
        try: return identity(os.fstat(fd))
        finally: os.close(fd)

    def verify(self):
        if (identity(os.fstat(self.fd))[:5] != self.directory_identity
                or identity(self.path.lstat())[:5] != self.directory_identity):
            raise ValueError('Original process directory generation changed')
        for name, (host, host_fd, expected) in self.hosts.items():
            values = [identity(os.fstat(host_fd)), identity(host.stat()), identity(os.fstat(self.targets[name]))]
            current_fd = os.open(name, os.O_PATH, dir_fd=self.fd)
            try: values.append(identity(os.fstat(current_fd)))
            finally: os.close(current_fd)
            if name != 'exe': values = [v[:5] for v in values]
            if any(v != expected for v in values):
                raise ValueError('Original process namespace/root/executable changed')
        if process_birth(self.read('stat', 8192), self.pid) != self.birth:
            raise ValueError('Original process birth changed')


def lease_document(raw):
    def unique(rows):
        result = {}
        for key, value in rows:
            if key in result: raise ValueError('Duplicate native lease metadata')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def require_no_portable():
    if any(os.path.lexists(p) for p in ('/etc/droiddeck/runtime.json', '/var/lib/droiddeck-capture-loader', '/var/lib/droiddeck-capture-switch')):
        raise ValueError('Portable/capture enrollment is not an original legacy baseline')


def inspect():
    if sys.platform != 'linux' or os.geteuid() != 0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
        raise ValueError('Root Linux fixed system bus required')
    require_no_portable()
    with ExitStack() as stack:
        held = Held(stack)
        sources = {}
        for name in PINS:
            path = ROOT / name
            mode = stat.S_IMODE(path.lstat().st_mode)
            if mode not in (0o444, 0o644): raise ValueError('Original protected source mode required')
            sources[name] = hashlib.sha256(held.read(path, 262144, mode)).hexdigest()
        lease = lease_document(held.read(LEASE, 512, 0o600))
        before = unit_fields()
        if not re.fullmatch('[1-9][0-9]{1,9}', before.get('MainPID', '')):
            raise ValueError('Live original video PID required')
        pid = int(before['MainPID']); process = Process(stack, pid)
        command = process.read('cmdline', 4096)
        original_environment = process.read('environ', 131072)
        env = environment(original_environment)
        result = validate(sources, before, command, env, lease)
        for path, (_, expected) in held.files.items():
            if path in (ROOT / n for n in PINS):
                if process.source_identity(path) != expected:
                    raise ValueError('Original process runtime namespace differs')
        names = ['tesla-waydroid-' + n + '.service' for n in ('runtime', 'compositor', 'video-preview', 'audio-preview', 'gateway-preview', 'video-relay-preview', 'audio-relay-preview')]
        if collect(['is-active', *names]).splitlines() != ['active'] * 7:
            raise ValueError('Healthy original runtime/media peers required')
        if (unit_fields() != before or process.read('cmdline', 4096) != command
                or process.read('environ', 131072) != original_environment):
            raise ValueError('Original unit/process generation changed')
        process.verify()
        held.verify()
        require_no_portable()
        process.verify()
        return {**result, 'pid': pid, 'startTicks': process.birth, 'all7Active': True, 'protectedGenerationsVerified': True}


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['inspect']: raise ValueError('Observation-only inspect required')
        print(json.dumps(inspect(), sort_keys=True, indent=2))
    except Exception as error:
        print('Legacy GPU preflight refused; original state preserved: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
