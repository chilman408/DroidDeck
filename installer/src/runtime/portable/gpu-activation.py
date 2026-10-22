# SPDX-License-Identifier: GPL-3.0-only
"""Disposable-VM GPU activation; independently supervised, with no healthy expiry.

This is a pinned installation-development component, not a released installer.
Opaque Android property documents never leave their original private directory.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import types
import uuid

ROOT = Path('/opt/droiddeck/gpu-recovery')
STATE = Path('/var/lib/droiddeck/gpu-activation')
WORK = Path('/var/lib/waydroid')
PROFILE = Path('/var/lib/droiddeck/waydroid-profile')
WORKER = 'droiddeck-gpu-activation.service'
WATCHER = 'droiddeck-gpu-recovery.service'
UNITS = ('tesla-waydroid-runtime.service', 'tesla-waydroid-headless-session.service',
         'waydroid-container.service', 'tesla-waydroid-compositor.service')
PINS = {
    'runtime/portable/promote-images.py': '44fae10ee5cd07d633e77140c9c224a4251909ad8922cdb186c9c8b698cfaf75',
    'runtime/portable/stage-images.py': '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a',
    'tools/waydroid-layer-property.py': '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f',
    'tools/waydroid-gpu-health-policy.py': '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e',
    'runtime/portable/gpu-native.js': '37f759fa8e484a8a33e72392ac5a3df5a554a4439992b64a740878aec70f5c26',
}
CPU = '40e24715fcd8c3e9bc1003c934fc93466f6cfdac18b8496da35ed13bd6def0ba'
GPU = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'
STAGES = frozenset({'prepared', 'installing', 'starting', 'verifying-native',
                    'ready', 'recovering', 'restored', 'recovery-required'})
ON = 'pending-droiddeck-layer-activation-on'
OFF = 'pending-droiddeck-layer-activation-off'
CFG_ON = 'pending-droiddeck-gpu-activation.cfg'
CFG_OFF = 'pending-droiddeck-gpu-activation-restore.cfg'
ALLOC = 'mesa-allocator-activation'
SCOPE = 'activation'
READY = 'pending-droiddeck-allocator-activation-ready'
RESTORED = 'pending-droiddeck-allocator-activation-restored'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def protected_read(path, mode=0o600, maximum=1048576):
    """No symlinks, writable ancestors, hard links, or partial generations."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute protected path required')
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        ancestors = [(Path('/'), fd, os.fstat(fd))]
        current = Path('/')
        for part in path.parts[1:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsafe protected ancestor')
            current /= part
            ancestors.append((current, fd, info))
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stack.callback(os.close, leaf)
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (0, 0, mode) or
                not 0 < before.st_size <= maximum):
            raise ValueError('Unsafe protected file')
        data = os.read(leaf, maximum + 1)
        fields = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns,
                            i.st_nlink, i.st_uid, i.st_gid, i.st_mode)
        if any(fields(before) != fields(i) for i in (os.fstat(leaf), os.stat(path.name, dir_fd=fd, follow_symlinks=False))) or len(data) != before.st_size:
            raise ValueError('Protected generation changed')
        for name, descriptor, original in ancestors:
            for i in (name.lstat(), os.fstat(descriptor)):
                if (not stat.S_ISDIR(i.st_mode) or (i.st_dev, i.st_ino) != (original.st_dev, original.st_ino)
                        or i.st_uid != 0 or i.st_gid != 0 or i.st_mode & 0o022):
                    raise ValueError('Protected ancestor changed')
        return data


def load_module(path, pin):
    data = protected_read(path)
    if sha(data) != pin:
        raise ValueError('GPU recovery source pin changed')
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def sources():
    for relative, pin in PINS.items():
        if sha(protected_read(ROOT / relative)) != pin:
            raise ValueError('GPU recovery source changed')
    return (load_module(ROOT / 'runtime/portable/promote-images.py', PINS['runtime/portable/promote-images.py']),
            load_module(ROOT / 'tools/waydroid-gpu-health-policy.py', PINS['tools/waydroid-gpu-health-policy.py']))


def command(args, timeout=45, check=True):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=check)


def unit(unit_name):
    raw = command(['/usr/bin/systemctl', 'show', unit_name, '-p', 'ActiveState',
                   '-p', 'MainPID', '-p', 'ControlPID']).stdout.decode('ascii')
    result = dict(line.split('=', 1) for line in raw.splitlines())
    if set(result) != {'ActiveState', 'MainPID', 'ControlPID'} or any(
            not result[key].isdigit() for key in ('MainPID', 'ControlPID')):
        raise ValueError('Invalid service identity')
    return result


def require_quiet(state):
    if state['ActiveState'] not in ('inactive', 'failed') or any(
            state[key] != '0' for key in ('MainPID', 'ControlPID')):
        raise ValueError('Activation worker or service is not quiescent')


def stop_worker():
    command(['/usr/bin/systemctl', 'stop', WORKER], timeout=70)
    require_quiet(unit(WORKER))


def watcher_armed():
    state = unit(WATCHER)
    if state['ActiveState'] != 'active' or int(state['MainPID']) < 2:
        raise ValueError('Independent GPU recovery is not armed')
    worker = unit(WORKER)
    if str(os.getpid()) not in (worker['MainPID'], worker['ControlPID']):
        raise ValueError('Activation must run in its supervised systemd unit')


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def uptime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def validate_status(value):
    if (not isinstance(value, dict) or set(value) != {'stage', 'boot', 'since'} or
            value['stage'] not in STAGES or not isinstance(value['boot'], str) or
            len(value['boot']) != 36 or type(value['since']) not in (int, float) or
            not 0 <= value['since'] < 1e12):
        raise ValueError('Invalid GPU recovery status')
    return value


class Store:
    def __init__(self, p, copier):
        self.p, self.c = p, copier

    @contextmanager
    def directory(self):
        with ExitStack() as stack:
            chain = self.c.open_chain(stack, STATE, root_owned=True)
            parent = chain[-1][1]
            self.c.protected(os.fstat(parent), 0o700)
            yield parent
            self.c.check_chain(chain, root_owned=True)

    def write(self, name, value, exclusive=False):
        data = (json.dumps(value, sort_keys=True) + '\n').encode()
        with self.directory() as parent:
            tmp = name if exclusive else '.pending-' + name + '-' + uuid.uuid4().hex
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                os.fchmod(fd, 0o600)
                self.c.write_all(fd, data)
                os.fsync(fd)
                if not exclusive:
                    protected_read(STATE / name)
                    os.replace(tmp, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(fd)

    def read(self, name):
        with self.directory():
            return json.loads(protected_read(STATE / name))

    def status(self):
        return validate_status(self.read('status.json'))

    def stage(self, value, exclusive=False):
        self.write('status.json', validate_status({'stage': value, 'boot': boot_id(), 'since': uptime()}), exclusive)

    @contextmanager
    def lock(self):
        with self.directory() as parent:
            fd = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
            try:
                self.c.protected(os.fstat(fd), 0o600)
                if os.fstat(fd).st_nlink != 1:
                    raise ValueError('Foreign recovery lock')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            finally:
                os.close(fd)


class Activation:
    SESSION_START_TIMEOUT = 70

    def __init__(self, p):
        self.p, self.c = p, p.load_copier()
        self.store = Store(p, self.c)
        self.layer = p._load_layer_parser(self.c)

    def offline(self, stack):
        return self.p._offline_layer_context(stack, self.c)

    def profiles(self):
        original = protected_read(PROFILE / 'original.cfg')
        if sha(original) != self.p.WAYDROID_CONFIGURATION_SHA256:
            raise ValueError('Original GPU profile source changed')
        result = tuple(self.p.profile_with_detected_protocols(self.p.profile_candidate(original, gpu=gpu)) for gpu in (False, True))
        if tuple(map(sha, result)) != (CPU, GPU):
            raise ValueError('Derived GPU/CPU profiles changed')
        self.p._assert_pid_metadata(self.c)
        self.p._verify_profile_codecs(self.c)
        return result

    def selected(self, context):
        # Root-private Android directory may have user-owned ancestors; use the held copier context.
        with ExitStack() as stack:
            context['check']()
            chain = self.c.open_chain(stack, context['property'])
            fd, before, data = self.p._read_profile_file(self.c, stack, chain[-1][1], 'persistent_properties', maximum=self.layer.MAX_BYTES)
            value = self.layer.saved_value(data)
            self.c.check_image(chain[-1][1], 'persistent_properties', fd, before, private=True)
            context['check']()
            return value

    def manifest(self, context):
        value = self.store.read('manifest.json')
        expected = {'version': 1, 'cpu': CPU, 'gpu': GPU, 'beforeLayer': 'absent',
                    'dataDevice': context['dataDevice'], 'dataInode': context['dataInode']}
        if value != expected or any(type(value.get(k)) is not int for k in ('version', 'dataDevice', 'dataInode')):
            raise ValueError('GPU transaction admission changed')
        return value

    def prepare(self):
        self.p._assert_codec_stopped()
        require_quiet(unit(WORKER))
        self.profiles()
        if sha(protected_read(WORK / 'waydroid.cfg', 0o644)) != CPU:
            raise ValueError('Fresh stopped CPU baseline required')
        with ExitStack() as stack:
            ctx = self.offline(stack)
            if self.selected(ctx) != 'absent':
                raise ValueError('Fresh saved-layer absence required')
            misc = ctx['property'].parent / 'misc'
            for path in (misc / 'waydroid_settings', misc / READY, misc / RESTORED,
                         Path('/var/lib/droiddeck') / ALLOC, Path('/var/lib/droiddeck') / ('pending-' + ALLOC),
                         ctx['property'] / ON, ctx['property'] / OFF, WORK / CFG_ON, WORK / CFG_OFF):
                if os.path.lexists(path):
                    raise ValueError('GPU activation namespace already exists; preserve it')
            with ExitStack() as parents:
                chain = self.c.open_chain(parents, STATE.parent, root_owned=True)
                os.mkdir(STATE.name, 0o700, dir_fd=chain[-1][1])
                os.fsync(chain[-1][1])
            self.store.write('manifest.json', {'version': 1, 'cpu': CPU, 'gpu': GPU,
                'beforeLayer': 'absent', 'dataDevice': ctx['dataDevice'], 'dataInode': ctx['dataInode']}, True)
            self.store.stage('prepared', True)

    def allocator_restore(self, ctx):
        misc = ctx['property'].parent / 'misc'
        parent = Path('/var/lib/droiddeck')
        names = [os.path.lexists(misc / name) for name in ('waydroid_settings', READY, RESTORED)]
        receipt = parent / ALLOC / 'receipt.json'
        if not any(names) and not os.path.lexists(parent / ALLOC) and not os.path.lexists(parent / ('pending-' + ALLOC)):
            return  # Activation did not reach allocator staging.
        if names == [False, False, True]:
            with ExitStack() as stack:
                chain = self.c.open_chain(stack, misc)
                metadata = json.loads(protected_read(receipt))
                identity = {key: ctx[key] for key in ('dataDevice', 'dataInode')}
                self.p._allocator_metadata(metadata, identity, os.fstat(chain[-1][1]), SCOPE)
                fd, info, raw = self.p._read_profile_file(self.c, stack, chain[-1][1], RESTORED)
                if raw != self.p.ALLOCATOR_CODE or (info.st_dev, info.st_ino) != (metadata['fileDevice'], metadata['fileInode']):
                    raise ValueError('Foreign retained allocator')
                ctx['check']()
            return
        self.p._configure_mesa_allocator(self.c, misc, parent,
            {key: ctx[key] for key in ('dataDevice', 'dataInode')}, restore=True,
            check_context=ctx['check'], scope=SCOPE)

    def change(self, restoring=False):
        self.p._assert_codec_stopped()
        cpu, gpu = self.profiles()
        with ExitStack() as stack:
            ctx = self.offline(stack)
            self.manifest(ctx)
            current = sha(protected_read(WORK / 'waydroid.cfg', 0o644))
            if restoring and current == GPU:
                if protected_read(WORK / CFG_ON, 0o644) != cpu:
                    raise ValueError('GPU transaction does not own displaced CPU config')
                self.p._exchange_profile(self.c, WORK / 'waydroid.cfg', cpu, GPU, CFG_OFF,
                    guard=self.p._assert_codec_stopped, check_source=ctx['check'])
            elif not restoring and current == CPU:
                self.p._exchange_profile(self.c, WORK / 'waydroid.cfg', gpu, CPU, CFG_ON,
                    guard=self.p._assert_codec_stopped, check_source=ctx['check'])
            elif not restoring or current != CPU:
                raise ValueError('Foreign GPU configuration; no overwrite')
            selected = self.selected(ctx)
            if selected == ('true' if restoring else 'absent'):
                self.p._edit_saved_layer(self.c, self.layer, ctx['property'], expected=selected,
                    target='absent' if restoring else 'true', pending=OFF if restoring else ON,
                    recovery_source=ON, guard=self.p._assert_codec_stopped, check_context=ctx['check'])
            elif not restoring or selected != 'absent':
                raise ValueError('Foreign saved-layer state')
            if restoring:
                self.allocator_restore(ctx)
            else:
                self.p._configure_mesa_allocator(self.c, ctx['property'].parent / 'misc', Path('/var/lib/droiddeck'),
                    {key: ctx[key] for key in ('dataDevice', 'dataInode')}, check_context=ctx['check'], scope=SCOPE)
            ctx['check']()
        command(['/usr/bin/waydroid', 'upgrade', '-o'], timeout=120)
        self.p._assert_codec_stopped()

    def native(self):
        result = command(['/usr/bin/node', str(ROOT / 'runtime/portable/gpu-native.js')], timeout=35, check=False)
        if result.returncode != 0:
            return False
        evidence = json.loads(result.stdout)
        return evidence.get('nativeGpuVerified') is True

    def activate(self):
        watcher_armed()
        with self.store.lock():
            if self.store.status()['stage'] != 'prepared':
                raise ValueError('GPU activation is single-use, not replayable')
            try:
                self.store.stage('installing')
                self.change()
                watcher_armed()
                self.store.stage('starting')
                command(['/usr/bin/systemctl', 'start', UNITS[3]], timeout=70)
                command(['/usr/bin/systemctl', 'start', UNITS[2], UNITS[1]], timeout=self.SESSION_START_TIMEOUT)
                self.store.stage('verifying-native')
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    watcher_armed()
                    if self.native():
                        self.store.stage('ready')
                        print('Android native GPU verified; independent recovery remains armed', flush=True)
                        return
                    time.sleep(5)
                raise ValueError('Native GPU startup did not verify')
            except Exception:
                self.store.stage('recovery-required')
                raise

    def recover(self):
        stop_worker()  # Must terminate all tracked mutators before acquiring their lock.
        with self.store.lock():
            command(['/usr/bin/systemctl', 'stop', *UNITS], timeout=90)
            stopped = {name: unit(name) for name in UNITS}
            for state in stopped.values():
                require_quiet(state)
            # A terminated worker can be quiescent but failed. Normalize only these
            # verified stopped units before entering the inactive-only data guard.
            failed = [name for name, state in stopped.items() if state['ActiveState'] == 'failed']
            if failed:
                command(['/usr/bin/systemctl', 'reset-failed', *failed], timeout=15)
                for name in UNITS:
                    require_quiet(unit(name))
            self.store.stage('recovering')
            self.change(restoring=True)
            self.store.stage('restored')
            print('CPU profile restored; disposable VM workers left stopped', flush=True)

    def watch(self, policy):
        failures = 0
        while True:
            state = self.store.status()
            age = max(0, uptime() - state['since']) if state['boot'] == boot_id() else policy.STARTUP_LIMIT_SEC
            healthy = False
            if state['stage'] == 'ready':
                try:
                    healthy = self.native()
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            action, failures = policy.decision(state['stage'], healthy, failures, age)
            if action == 'stop':
                return
            if action == 'recover':
                self.recover()
                return
            time.sleep(15)


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or args not in (["--prepare"], ["--activate"], ["--watch"], ["--recover"]):
        raise ValueError('Root Linux fixed GPU lifecycle command required')
    if Path(__file__) != ROOT / 'runtime/portable/gpu-activation.py':
        raise ValueError('Protected GPU recovery installation required')
    protected_read(Path(__file__))
    p, policy = sources()
    activation = Activation(p)
    if args == ['--watch']:
        activation.watch(policy)
    else:
        getattr(activation, args[0][2:])()


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        # Never emit exception text that could contain private Android documents.
        print('GPU lifecycle refused or failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
