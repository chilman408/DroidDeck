# SPDX-License-Identifier: GPL-3.0-only
"""Single-use headless drop-in migration; systemd ExecStopPost recovers interruption."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import types
import uuid

ROOT = Path('/opt/droiddeck/headless-compat-20261014bm')
STATE = Path('/var/lib/droiddeck/headless-compat-20261014bm')
SYSTEM = Path('/etc/systemd/system')
UNIT = 'tesla-waydroid-headless-session.service'
TARGET = UNIT + '.d'
PENDING = 'pending-droiddeck-headless-compat-20261014bm'
RETIRED = 'retired-droiddeck-headless-compat-20261014bm'
WORKER = 'droiddeck-headless-migration-20261014bm.service'
COMPOSITOR = 'tesla-waydroid-compositor.service'
COMPOSITOR_HASH = '642d7eedda85a550a0a86d1222fe7718920f27f4191096bd8e642a65ce509119'
BASE = 'b7efd8b4be74df80bd350396017d8137fe36dd62fb13f66a78023dfa169ca978'
WRAPPER = '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba'
GPU = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'
CONFIG = Path('/var/lib/waydroid/waydroid.cfg')
GPU_SOURCE = Path('/opt/droiddeck/gpu-recovery/runtime/portable/gpu-activation.py')
GPU_SOURCE_HASH = 'f2195b108f87d443d890bb26187da2aa4b4aa6fec37644d01c8a77788edcbd1a'
DROPIN = ('[Service]\nType=notify\nNotifyAccess=main\nExecStart=\n'
          'ExecStart=/usr/bin/python3 -B ' + str(ROOT) + '/headless-session.py\n'
          'TimeoutStartSec=60s\n').encode()


def read(path, maximum=1048576):
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError('Protected migration path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or (before.st_uid, before.st_gid) != (0, 0)
                or before.st_mode & 0o022 or not 0 < before.st_size <= maximum):
            raise ValueError('Protected migration file required')
        data = os.read(fd, maximum + 1)
        fields = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns, i.st_mode, i.st_uid, i.st_gid, i.st_nlink)
        if len(data) != before.st_size or any(fields(i) != fields(before) for i in (os.fstat(fd), path.lstat())):
            raise ValueError('Migration file changed')
        return data
    finally:
        os.close(fd)


def digest(path):
    return hashlib.sha256(read(path)).hexdigest()


def command(args, timeout=80):
    return subprocess.run(args, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout).stdout.decode().strip()


def unit(name):
    return dict(line.split('=', 1) for line in command(['/usr/bin/systemctl', 'show', name,
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID', '-p', 'SubState']).splitlines())


def gpu_store():
    data = read(GPU_SOURCE)
    if hashlib.sha256(data).hexdigest() != GPU_SOURCE_HASH:
        raise ValueError('Immutable GPU recovery source changed')
    module = types.ModuleType('headless_gpu_coordination')
    module.__file__ = str(GPU_SOURCE)
    exec(compile(data, str(GPU_SOURCE), 'exec'), module.__dict__)
    promoter, _ = module.sources()
    # Use only the existing lock/status API; never replay activation or recovery.
    return module.Activation(promoter).store


def native():
    report = json.loads(command(['/usr/bin/node', '/opt/droiddeck/gpu-recovery/runtime/portable/gpu-native.js'], timeout=40))
    return report.get('nativeGpuVerified') is True


def wait_native():
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if native(): return True
        except (ValueError, subprocess.SubprocessError):
            pass
        time.sleep(2)
    return False


def empty_session():
    import dbus
    bus = dbus.SystemBus()
    service = dbus.Interface(bus.get_object('id.waydro.Container', '/ContainerManager'),
                             'id.waydro.ContainerManager')
    # Do not export session dictionaries, user paths, environment or app data.
    return not bool(service.GetSession(timeout=5))


def compositor_ready():
    path = Path('/run/user/1000/wayland-teslaandroid')
    before = path.lstat()
    if not stat.S_ISSOCK(before.st_mode) or before.st_uid != 1000:
        raise ValueError('Private compositor socket unavailable')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(path))
    after = path.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError('Compositor socket changed')


def cold_start():
    # Quiesce all PartOf peers together. A compositor crash/restart otherwise
    # restarts the session alone while the root daemon still owns its first PID.
    command(['/usr/bin/systemctl', 'stop', UNIT, 'waydroid-container.service', COMPOSITOR])
    for name in (UNIT, 'waydroid-container.service', COMPOSITOR):
        state = unit(name)
        if state['MainPID'] != '0' or state['ControlPID'] != '0':
            raise ValueError('Old startup graph not quiescent')
    command(['/usr/bin/systemctl', 'start', COMPOSITOR])
    deadline = time.monotonic() + 10
    while True:
        try:
            compositor_ready()
            break
        except (OSError, ValueError):
            if time.monotonic() >= deadline: raise ValueError('Compositor startup unavailable')
            time.sleep(0.1)
    compositor = unit(COMPOSITOR)
    if compositor['ActiveState'] != 'active' or int(compositor['MainPID']) < 2:
        raise ValueError('Fresh compositor not active')
    command(['/usr/bin/systemctl', 'start', 'waydroid-container.service'])
    if not empty_session():
        raise ValueError('Fresh root daemon already tracks a session; no duplicate Start')
    command(['/usr/bin/systemctl', 'start', UNIT], timeout=75)
    if unit(COMPOSITOR)['MainPID'] != compositor['MainPID']:
        raise ValueError('Compositor changed during session startup')


def container():
    raw = command(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])
    if not raw.isdigit() or int(raw) < 2:
        raise ValueError('Running Android container required')
    # Process birth disambiguates PID reuse without reading Android data.
    data = Path('/proc/' + raw + '/stat').read_text()
    fields = data.rpartition(') ')[2].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError('Container identity unavailable')
    return [int(raw), int(fields[19])]


def session_arguments(pid):
    return Path('/proc/' + pid + '/cmdline').read_bytes().split(b'\0')[:-1]


def write_exclusive(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data); stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def publish(source, target):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    sync_dir(SYSTEM)


def record(value):
    path = STATE / ('pending-' + uuid.uuid4().hex)
    write_exclusive(path, (json.dumps(value, sort_keys=True) + '\n').encode(), 0o600)
    os.replace(path, STATE / 'receipt.json')
    sync_dir(STATE)


def owned(value, path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o755)
            or [info.st_dev, info.st_ino] != value['directory'] or sorted(os.listdir(path)) != ['60-droiddeck-compat.conf']
            or read(path / '60-droiddeck-compat.conf') != DROPIN):
        raise ValueError('Foreign headless drop-in; no overwrite')


def preflight():
    if digest(SYSTEM / UNIT) != BASE or digest(ROOT / 'headless-session.py') != WRAPPER:
        raise ValueError('Headless source or baseline changed')
    if digest(CONFIG) != GPU:
        raise ValueError('GPU profile changed')
    if digest(SYSTEM / COMPOSITOR) != COMPOSITOR_HASH:
        raise ValueError('Compositor baseline changed')
    for name in ('droiddeck-gpu-recovery.service', 'waydroid-container.service', 'tesla-waydroid-compositor.service'):
        state = unit(name)
        if state['ActiveState'] != 'active' or int(state['MainPID']) < 2:
            raise ValueError('Protected baseline not active')
    headless = unit(UNIT)
    if headless['ActiveState'] not in ('active', 'inactive') or headless['ControlPID'] != '0':
        raise ValueError('Headless baseline not quiescent or active')
    runtime = unit('tesla-waydroid-runtime.service')
    if runtime['ActiveState'] != 'inactive' or runtime['MainPID'] != '0' or runtime['ControlPID'] != '0':
        raise ValueError('Preview runtime must remain stopped')
    worker = unit(WORKER)
    if str(os.getpid()) not in (worker['MainPID'], worker['ControlPID']):
        raise ValueError('Supervised migration worker required')


def apply():
    preflight()
    for path in (STATE, SYSTEM / TARGET, SYSTEM / PENDING, SYSTEM / RETIRED):
        if os.path.lexists(path):
            raise ValueError('Single-use migration namespace exists; inspect it')
    identity = container()
    STATE.mkdir(mode=0o700)
    sync_dir(STATE.parent)
    (SYSTEM / PENDING).mkdir(mode=0o755)
    os.chmod(SYSTEM / PENDING, 0o755)
    info = (SYSTEM / PENDING).lstat()
    value = {'version': 1, 'stage': 'prepared', 'directory': [info.st_dev, info.st_ino], 'beforeContainer': identity}
    record(value)
    write_exclusive(SYSTEM / PENDING / '60-droiddeck-compat.conf', DROPIN, 0o644)
    sync_dir(SYSTEM / PENDING)
    preflight()
    owned(value, SYSTEM / PENDING)
    publish(SYSTEM / PENDING, SYSTEM / TARGET)
    value['stage'] = 'installed'; record(value)
    store = gpu_store()
    with store.lock():
        if store.status()['stage'] != 'ready':
            raise ValueError('Ready GPU guard required before cold transition')
        # Existing bounded startup phase, NOT a deadline on a healthy accepted GPU.
        store.stage('verifying-native')
        command(['/usr/bin/systemctl', 'daemon-reload'])
        cold_start()
        current, after = unit(UNIT), container()
        if current['ActiveState'] != 'active' or current['SubState'] != 'running' or int(current['MainPID']) < 2 or after == identity:
            raise ValueError('Callback-ready cold session did not verify')
        argv = session_arguments(current['MainPID'])
        if argv != [b'/usr/bin/python3', b'-B', os.fsencode(ROOT / 'headless-session.py')]:
            raise ValueError('Unexpected headless executable')
        if not wait_native() or digest(CONFIG) != GPU or container() != after:
            raise ValueError('Native GPU or cold container identity did not verify')
        owned(value, SYSTEM / TARGET)
        store.stage('ready')
        value.update(stage='accepted', sessionPid=int(current['MainPID']), container=after)
        record(value)
    print('Headless compatibility callback ready; native GPU retained; no healthy expiry', flush=True)


def recover_pending():
    if not STATE.exists():
        return
    value = json.loads(read(STATE / 'receipt.json', 4096))
    if value.get('stage') in ('accepted', 'restored'):
        return
    if (value.get('version') != 1 or value.get('stage') not in ('prepared', 'installed') or
            digest(SYSTEM / UNIT) != BASE):
        raise ValueError('Unknown migration recovery state')
    target = SYSTEM / TARGET
    if os.path.lexists(target):
        owned(value, target)
        command(['/usr/bin/systemctl', 'stop', UNIT])
        owned(value, target)
        publish(target, SYSTEM / RETIRED)
    if os.path.lexists(SYSTEM / RETIRED):
        owned(value, SYSTEM / RETIRED)
        command(['/usr/bin/systemctl', 'daemon-reload'])
        # Never fight independent GPU recovery if it has changed the profile.
        if digest(CONFIG) == GPU and unit('droiddeck-gpu-recovery.service')['ActiveState'] == 'active':
            store = gpu_store()
            with store.lock():
                if store.status()['stage'] in ('ready', 'verifying-native'):
                    store.stage('verifying-native')
                    try:
                        cold_start()
                        if not wait_native(): raise ValueError('Original startup native health failed')
                        store.stage('ready')
                    except Exception:
                        store.stage('recovery-required')
                        raise
    value['stage'] = 'restored'; record(value)
    print('Unaccepted headless migration restored; owned drop-in retained', flush=True)


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or Path(__file__) != ROOT / 'headless-transition.py' or
            sys.argv[1:] not in (['--apply'], ['--recover-pending'])):
        raise ValueError('Protected fixed migration command required')
    read(Path(__file__))
    (apply if sys.argv[1] == '--apply' else recover_pending)()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Headless migration incomplete: ' + type(error).__name__ + '; inspect retained state', file=sys.stderr)
        sys.exit(1)
