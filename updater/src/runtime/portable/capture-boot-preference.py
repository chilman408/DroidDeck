# SPDX-License-Identifier: GPL-3.0-only
"""One GPU admission per boot through the independently enrolled signed loader.

Candidate component: enrollment and real reboot qualification precede rollout.
Never imports candidate code, changes Android data, edits the sealed base, holds
the capture/updater lock, or restores another operation. An uncertain admission
is never replayed. Only the original signed controller owns rollback/recovery.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import selectors

ROOT = Path('/var/lib/droidmate-gpu-default')
LOADER = Path('/var/lib/droiddeck-capture-loader/bootstrap.py')
SOCKET = Path('/var/lib/droiddeck-capture-switch/control.sock')
STATE = SOCKET.parent / 'state.json'
SERVICE = Path('/etc/systemd/system/droidmate-gpu-default.service')
RECOVERY_TEMPLATE = SERVICE.parent / 'droiddeck-capture-recover@.service'
UUID = r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def unique(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise ValueError('Duplicate preference metadata')
            out[key] = value
        return out
    value = json.loads(raw, object_pairs_hook=unique)
    if encode(value) != raw:
        raise ValueError('Canonical preference metadata required')
    return value


def preference(raw):
    p = document(raw)
    if (type(p) is not dict or set(p) != {'schema', 'kind', 'packageSha256', 'viewToken', 'loaderSha256', 'machineSha256'}
            or type(p['schema']) is not int or p['schema'] != 1 or p['kind'] != 'droidmate-gpu-boot-preference'):
        raise ValueError('Closed GPU boot preference required')
    for key in ('packageSha256', 'loaderSha256', 'machineSha256', 'viewToken'):
        if type(p[key]) is not str or not re.fullmatch('[a-f0-9]{' + ('32' if key == 'viewToken' else '64') + '}', p[key]):
            raise ValueError('Exact package/view/loader/machine binding required')
    return p


def unit(preference_sha, package):
    if not all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) for v in (preference_sha, package)):
        raise ValueError('Exact enrollment pins required')
    recovery = 'droiddeck-capture-recover@' + package + '.service'
    return ('[Unit]\nDescription=DroidMate preferred GPU capture after owned recovery\n'
            'After=' + recovery + ' droiddeck-update-boot.service tesla-waydroid-runtime.service\n'
            'ConditionPathExists=' + str(ROOT / 'armed.json') + '\n'
            '[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 -I -B ' + str(ROOT / 'program.py')
            + ' run ' + preference_sha + '\nRestart=no\nTimeoutStartSec=360\nRemainAfterExit=yes\n').encode('ascii')


def handoff_path(package):
    if type(package) is not str or not re.fullmatch('[a-f0-9]{64}', package):
        raise ValueError('Exact matching recovery instance required')
    return SERVICE.parent / ('droiddeck-capture-recover@' + package + '.service.d') / '90-droidmate-gpu-default.conf'


def handoff():
    # A failed recovery may retry. Only its final successful completion schedules
    # the helper, which must never pull/restart that recovery in return.
    return b'[Unit]\nOnSuccess=droidmate-gpu-default.service\nOnSuccessJobMode=fail\n'


def arming(enrollment_sha, preference_sha):
    if not all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) for v in (enrollment_sha, preference_sha)):
        raise ValueError('Exact completed enrollment and preference required')
    return {'schema': 1, 'kind': 'droidmate-gpu-boot-armed',
            'enrollmentSha256': enrollment_sha, 'preferenceSha256': preference_sha}


def recovery_unit():
    return ('[Unit]\nDescription=DroidDeck owned capture recovery\nAfter=network.target droiddeck-update-boot.service tesla-waydroid-runtime.service\nStartLimitIntervalSec=0\n'
            '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 -I -B ' + str(LOADER) + ' recover %i\n'
            'Restart=on-failure\nRestartSec=3\nTimeoutStartSec=120\n[Install]\nWantedBy=multi-user.target\n').encode('ascii')


def recovered(fields, package, now, previous=None):
    fixed = {'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success', 'ExecMainStatus': '0',
             'UnitFileState': 'enabled', 'FragmentPath': str(RECOVERY_TEMPLATE),
             'DropInPaths': str(handoff_path(package)), 'OnSuccess': 'droidmate-gpu-default.service',
             'OnSuccessJobMode': 'fail',
             'ConditionResult': 'yes', 'AssertResult': 'yes'}
    if any(fields.get(k) != v for k, v in fixed.items()):
        raise ValueError('Exact enabled effective recovery owner required')
    if not re.fullmatch('[a-f0-9]{32}', fields.get('InvocationID', '')):
        raise ValueError('Completed recovery invocation required')
    try:
        start, end = (int(fields[k]) for k in ('ExecMainStartTimestampMonotonic', 'ExecMainExitTimestampMonotonic'))
    except (KeyError, ValueError):
        raise ValueError('Completed current-boot recovery required') from None
    if not 0 < start <= end <= int(now * 1000000):
        raise ValueError('Recovery must have completed this boot')
    command = '/usr/bin/python3 -I -B ' + str(LOADER) + ' recover ' + package
    pattern = (r'\{ path=/usr/bin/python3 ; argv\[\]=' + re.escape(command)
               + r' ; ignore_errors=no ; start_time=\[[^\]\r\n]+\] ; stop_time=\[[^\]\r\n]+\] ; pid=[1-9][0-9]* ; code=exited ; status=0 \}')
    if previous is not None:
        # daemon-reload legitimately resets ExecStart's command-local history.
        # Retain the strictly verified ORIGINAL proof only within this process;
        # neither a new invocation nor a fresh process can adopt the reset form.
        recovered(previous, package, now)
        if ({k: v for k, v in fields.items() if k != 'ExecStart'}
                != {k: v for k, v in previous.items() if k != 'ExecStart'}):
            raise ValueError('Original completed recovery invocation changed')
        reset = ('{ path=/usr/bin/python3 ; argv[]=' + command
                 + ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')
        if fields.get('ExecStart') not in (previous['ExecStart'], reset):
            raise ValueError('Original recovery command or exact reload reset required')
    elif not re.fullmatch(pattern, fields.get('ExecStart', '')):
        raise ValueError('Exact completed recovery command required')


def collect(args, seconds):
    """Linux control output is capped WHILE reading, including failed commands."""
    until = time.monotonic() + seconds
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LC_ALL': 'C'}) as child:
        output = [bytearray(), bytearray()]
        try:
            with selectors.DefaultSelector() as poll:
                for index, pipe in enumerate((child.stdout, child.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    poll.register(pipe, selectors.EVENT_READ, index)
                while poll.get_map():
                    remaining = until - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Control deadline exceeded')
                    for key, _events in poll.select(remaining):
                        raw = os.read(key.fileobj.fileno(), 4096)
                        if not raw:
                            poll.unregister(key.fileobj)
                        else:
                            output[key.data].extend(raw)
                            if len(output[key.data]) > 8192:
                                raise ValueError('Control output bound exceeded')
                remaining = until - time.monotonic()
                if remaining <= 0 or child.wait(timeout=remaining) != 0:
                    raise ValueError('Control failed or exceeded its deadline')
            if time.monotonic() > until:
                raise TimeoutError('Late control result refused')
            return bytes(output[0])
        finally:
            if child.poll() is None:
                child.kill()  # Only this Popen child; never the independent worker.
                child.wait(timeout=5)


def cpu_ready(status, socket_exists):
    # A restored GPU view is reported as mode=gpu even though the ORIGINAL CPU
    # worker is restored. Phase, lease and owner exit are the authoritative test.
    return (type(status) is dict and status.get('phase') in ('restored', 'no-operation')
            and status.get('updaterLeaseHeld', False) is False and not socket_exists)


def gpu_ready(status, socket_exists):
    return (type(status) is dict and status.get('phase') == 'accepted'
            and status.get('mode') == 'gpu' and status.get('updaterLeaseHeld') is True and socket_exists)


def run_once(b, p):
    p = preference(encode(p))
    boot = b.verify()
    if type(boot) is not str or not re.fullmatch(UUID, boot):
        raise ValueError('Current kernel boot identity required')
    binding = {'schema': 1, 'kind': 'droidmate-gpu-boot-result', 'bootId': boot,
               'preferenceSha256': sha(encode(p)), 'packageSha256': p['packageSha256'], 'viewToken': p['viewToken']}
    prior = b.read(boot)
    if prior is not None:
        if (type(prior) is not dict or set(prior) != set(binding) | {'phase'}
                or any(type(prior[k]) is not type(v) or prior[k] != v for k, v in binding.items())
                or prior['phase'] not in ('intent', 'gpu')):
            raise ValueError('Different boot/preference obligation preserved')
        if prior['phase'] == 'gpu':
            b.preferred()
            if not gpu_ready(b.status(), b.socket_exists()):
                raise ValueError('Completed GPU owner is no longer healthy; original recovery owns repair')
            return prior
        raise ValueError('Uncertain admission preserved; do not replay or adopt its outcome')
    if not cpu_ready(b.status(), b.socket_exists()):
        raise ValueError('Original recovery and owner exit must complete before GPU admission')
    b.save(boot, {**binding, 'phase': 'intent'})
    b.verify()
    # Do not infer terminal failure from a CPU snapshot after a timeout: an
    # asynchronously dispatched signed trial might still publish later. Any
    # exception preserves intent. Only the signed owner handles its rollback.
    b.change()
    b.preferred()
    if not gpu_ready(b.status(), b.socket_exists()):
        raise ValueError('New GPU owner did not remain accepted')
    result = {**binding, 'phase': 'gpu'}
    b.save(boot, result)
    return result


def identity(s):
    return [s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def publish_new(fd, pending, name):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(pending), fd, os.fsencode(name), 1) != 0:
        raise OSError(ctypes.get_errno(), 'Exclusive journal publication refused')


class Native:
    """Root-owned enrollment, exclusive coordinator lock; signed locks stay separate."""
    def __init__(self, stack, expected):
        if sys.platform != 'linux' or os.geteuid() != 0 or Path(__file__) != ROOT / 'program.py':
            raise ValueError('Independently enrolled root Linux entry required')
        if not re.fullmatch('[a-f0-9]{64}', expected):
            raise ValueError('External preference pin required')
        self.stack, self.held, self.dirs, self.records = stack, {}, {}, {}
        self.deadline = time.monotonic() + 300
        self.directory(ROOT)
        if stat.S_IMODE(ROOT.stat().st_mode) != 0o700:
            raise ValueError('Private enrollment directory required')
        raw = self.read_file(ROOT / 'preference.json', 4096)
        if sha(raw) != expected:
            raise ValueError('Enrolled preference changed')
        self.p = preference(raw)
        program = self.read_file(ROOT / 'program.py', 65536)
        service = self.read_file(SERVICE, 8192, 0o644)
        dropin = self.read_file(handoff_path(self.p['packageSha256']), 4096, 0o644)
        self.enrollment_raw = self.read_file(ROOT / 'complete.json', 4096)
        self.preference_sha = expected
        receipt = document(self.enrollment_raw)
        if (receipt != {'schema': 1, 'kind': 'droidmate-gpu-boot-enrollment', 'programSha256': sha(program),
                       'preferenceSha256': expected, 'unitSha256': sha(service), 'handoffSha256': sha(dropin)}
                or service != unit(expected, self.p['packageSha256']) or dropin != handoff()):
            raise ValueError('Original program and unit enrollment required')
        if sha(self.read_file(LOADER, 1048576)) != self.p['loaderSha256']:
            raise ValueError('Original independent signed loader required')
        if self.read_file(RECOVERY_TEMPLATE, 8192, 0o644) != recovery_unit():
            raise ValueError('Original signed-controller recovery template required')
        self.directory(ROOT / 'boots')
        lock = ROOT / 'lock'
        self.read_file(lock, 16)
        import fcntl
        fcntl.flock(self.held[lock][0], fcntl.LOCK_EX | fcntl.LOCK_NB)

    def armed(self):
        value = document(self.read_file(ROOT / 'armed.json', 4096))
        if value != arming(sha(self.enrollment_raw), self.preference_sha):
            raise ValueError('Original completed enrollment must be independently armed')
        self.verify()

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
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid, s.st_gid) != (0, 0) or s.st_mode & 0o022:
            raise ValueError('Root-owned protected ancestry required')
        self.dirs[path] = (fd, identity(s)[:5])
        return fd

    def read_file(self, path, limit, mode=0o600, hold=True):
        parent = self.directory(path.parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        retained = False
        try:
            s = os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or (s.st_uid, s.st_gid, s.st_nlink, stat.S_IMODE(s.st_mode)) != (0, 0, 1, mode)
                    or not 0 < s.st_size <= limit):
                raise ValueError('Root-owned single-link bounded input required')
            raw = os.pread(fd, limit + 1, 0)
            if (len(raw) != s.st_size or identity(s) != identity(os.fstat(fd))
                    or identity(s) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError('Stable bounded input required')
            if hold:
                self.held[path] = (fd, identity(s))
                self.stack.callback(os.close, fd)
                retained = True
            return raw
        finally:
            if not retained:
                os.close(fd)

    def verify(self):
        for path, (fd, expected) in self.dirs.items():
            if identity(os.fstat(fd))[:5] != expected or identity(path.lstat())[:5] != expected:
                raise ValueError('Enrollment directory generation changed')
        for path, (fd, expected) in self.held.items():
            if identity(os.fstat(fd)) != expected or identity(path.lstat()) != expected:
                raise ValueError('Enrollment source generation changed')
        if sha(Path('/etc/machine-id').read_bytes()) != self.p['machineSha256']:
            raise ValueError('Preference belongs to another machine')
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def command(self, args, timeout=70):
        self.verify()
        remaining = min(timeout, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError('Boot admission budget exhausted')
        raw = collect(args, remaining)
        self.verify()
        if time.monotonic() > self.deadline:
            raise TimeoutError('Late admission result refused')
        return raw

    def control(self, action):
        if action not in ('status', 'change'):
            raise ValueError('Preference may only inspect or request a fresh change')
        args = ['/usr/bin/python3', '-I', '-B', str(LOADER), action, self.p['packageSha256']]
        if action == 'change':
            args.append(self.p['viewToken'])
        return json.loads(self.command(args, 145 if action == 'change' else 75))

    def status(self):
        # Ordering alone is insufficient, especially for a manual invocation.
        # The original matching recovery owner must remain enabled and have
        # completed successfully; never steal a running/failed recovery job.
        recovery = 'droiddeck-capture-recover@' + self.p['packageSha256'] + '.service'
        names = ('ActiveState,SubState,Result,ExecMainStatus,UnitFileState,FragmentPath,DropInPaths,InvocationID,'
                 'ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic,ConditionResult,AssertResult,ExecStart,OnSuccess,OnSuccessJobMode')
        raw = self.command(['/usr/bin/systemctl', 'show', recovery, '--property=' + names])
        fields = dict(row.split('=', 1) for row in raw.decode('ascii').splitlines() if '=' in row)
        boot = self.verify()
        prior = getattr(self, '_recovery_proof', None)
        if prior is not None and prior[0] != boot:
            raise ValueError('Original recovery proof belongs to another boot')
        recovered(fields, self.p['packageSha256'], time.monotonic(), None if prior is None else prior[1])
        if prior is None:
            self._recovery_proof = (boot, dict(fields))
        return self.control('status')

    def change(self):
        return self.control('change')

    def preferred(self):
        # Read-only result validation, NEVER an authorization to restore/recover.
        value = document(self.read_file(STATE, 131072, hold=False))
        if (value.get('packageSha256') != self.p['packageSha256'] or value.get('viewToken') != self.p['viewToken']
                or value.get('boot') != self.verify()):
            raise ValueError('Preferred package/view is not the current operation')

    def socket_exists(self):
        return os.path.lexists(SOCKET)

    def read_record(self, name):
        path = ROOT / 'boots' / name
        try:
            before = identity(path.lstat())
        except FileNotFoundError:
            self.records[name] = None
            return None
        raw = self.read_file(path, 4096, hold=False)
        if before != identity(path.lstat()):
            raise ValueError('Boot journal changed while reading')
        value = document(raw)
        self.records[name] = (raw, before)
        return value

    def read(self, boot):
        if not re.fullmatch(UUID, boot):
            raise ValueError('Exact boot journal name required')
        intent = self.read_record(boot + '.json')
        complete = self.read_record(boot + '.complete.json')
        if intent is None:
            if complete is not None:
                raise ValueError('Completion without original intent preserved')
            return None
        if type(intent) is not dict or intent.get('phase') != 'intent':
            raise ValueError('Original immutable intent required')
        if complete is not None:
            expected = {'record': {**intent, 'phase': 'gpu'}, 'intentIdentity': self.records[boot + '.json'][1]}
            if type(complete) is not dict or complete != expected:
                raise ValueError('Completion must match the original immutable intent')
            return complete['record']
        return intent

    def journal_unchanged(self, boot):
        for name in (boot + '.json', boot + '.complete.json'):
            expected = self.records[name]
            path = ROOT / 'boots' / name
            if expected is None:
                if os.path.lexists(path):
                    raise ValueError('Foreign journal preserved')
            elif (identity(path.lstat()) != expected[1]
                  or self.read_file(path, 4096, hold=False) != expected[0]):
                raise ValueError('Different boot journal generation preserved')

    def save(self, boot, value):
        self.verify()
        if not re.fullmatch(UUID, boot):
            raise ValueError('Exact boot journal name required')
        if boot + '.json' not in self.records:
            self.read(boot)
        self.journal_unchanged(boot)
        phase = value.get('phase') if type(value) is dict else None
        if phase not in ('intent', 'gpu'):
            raise ValueError('Immutable intent or GPU completion required')
        intent_name = boot + '.json'
        if phase == 'gpu':
            original = self.records[intent_name]
            if original is None or value != {**document(original[0]), 'phase': 'gpu'}:
                raise ValueError('GPU completion requires its original intent')
        fd = self.directory(ROOT / 'boots')
        name = boot + ('.json' if phase == 'intent' else '.complete.json')
        pending = '.' + name + '.pending'
        raw = encode(value if phase == 'intent' else {'record': value, 'intentIdentity': original[1]})
        out = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(out)
                expected = identity(os.fstat(out))
        finally:
            os.close(out)
        self.journal_unchanged(boot)
        publish_new(fd, pending, name)
        os.fsync(fd)
        # Rename may update ctime; every other pinned property must still match
        # the actual staged inode. Never adopt a replaced file after publication.
        published = identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
        if published[:-1] != expected[:-1] or self.read_file(ROOT / 'boots' / name, 4096, hold=False) != raw:
            raise ValueError('Published journal was replaced; preserve unexpected state')
        self.records[name] = (raw, published)
        self.journal_unchanged(boot)

def main(args):
    if len(args) != 2 or args[0] not in ('check', 'run'):
        raise ValueError('check|run ENROLLED_PREFERENCE_SHA256 required')
    with ExitStack() as stack:
        b = Native(stack, args[1])
        b.verify()
        if args[0] == 'check':
            return {'enrollmentVerified': True, 'activated': False, 'packageSha256': b.p['packageSha256']}
        b.armed()
        return run_once(b, b.p)


if __name__ == '__main__':
    try:
        print(encode(main(sys.argv[1:])).decode().strip())
    except Exception as error:
        print('GPU boot preference refused; preserve independent recovery: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
