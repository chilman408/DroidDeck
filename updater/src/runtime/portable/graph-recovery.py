# SPDX-License-Identifier: GPL-3.0-only
"""Single-use disposable graph recovery with independently invoked stop cleanup."""
from contextlib import ExitStack
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

ROOT = Path('/opt/droiddeck/graph-recovery-20261015br')
STATE = Path('/var/lib/droiddeck/graph-recovery-20261015br')
UNIT = 'droiddeck-gpu-activation.service'
GUARD = 'droiddeck-gpu-recovery.service'
READY = 'droiddeck-gpu-boot-ready.service'
COMPOSITOR = 'tesla-waydroid-compositor.service'
CONTAINER = 'waydroid-container.service'
HEADLESS = 'tesla-waydroid-headless-session.service'
GRAPH = (READY, HEADLESS, CONTAINER, COMPOSITOR)
PHASES = ('armed', 'restarting', 'verifying', 'complete')


class Recovery:
    def __init__(self, store, receipts, *, boot, admit, switch, verify):
        self.store, self.receipts, self.boot = store, receipts, boot
        self.admit, self.switch, self.verify = admit, switch, verify

    def record(self, value):
        if (not isinstance(value, dict) or set(value) != {'version', 'boot', 'phase'} or
                type(value['version']) is not int or value['version'] != 1 or
                value['boot'] != self.boot() or value['phase'] not in PHASES):
            raise ValueError('Foreign recovery receipt')
        return value

    def restart(self):
        self.admit()
        with self.store.lock():
            self.admit()
            state = self.store.status()
            if self.receipts.read() is not None or state['stage'] != 'ready' or state['boot'] != self.boot():
                raise ValueError('Fresh accepted graph required')
            self.receipts.write({'version': 1, 'boot': self.boot(), 'phase': 'armed'}, exclusive=True)
            self.store.stage('starting')
        self.recover_pending()

    def recover_pending(self):
        value = self.receipts.read()
        if value is None:
            return
        value = self.record(value)
        if value['phase'] == 'complete':
            return
        self.admit()
        with self.store.lock():
            self.admit()
            state = self.store.status()
            if state['boot'] != self.boot() or state['stage'] not in ('ready', 'starting'):
                raise ValueError('Original watcher retains recovery ownership')
            if state['stage'] == 'ready' and value['phase'] == 'verifying':
                finalize = True
            else:
                finalize = False
                if state['stage'] == 'ready':
                    self.store.stage('starting')
                value = {**value, 'phase': 'restarting'}
                self.receipts.write(value)
            if not finalize:
                # Watcher stops this entire supervised worker before taking this lock.
                self.switch()
                value = {**value, 'phase': 'verifying'}
                self.receipts.write(value)
        self.verify()
        with self.store.lock():
            state = self.store.status()
            if state['boot'] != self.boot() or state['stage'] != 'ready':
                raise ValueError('Native graph did not become ready')
            self.receipts.write({**value, 'phase': 'complete'})


def protected_read(filename, maximum=1048576):
    filename = Path(filename)
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        chain = [(Path('/'), fd)]
        parent = Path('/')
        for name in filename.parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
            parent /= name
            chain.append((parent, fd))
        def directories():
            for name, descriptor in chain:
                opened, named = os.fstat(descriptor), name.lstat()
                if (not stat.S_ISDIR(named.st_mode) or named.st_uid != 0 or named.st_gid != 0 or named.st_mode & 0o022 or
                        (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                    raise ValueError('Protected recovery namespace changed')
        directories()
        leaf = os.open(filename.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stack.callback(os.close, leaf)
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0 or
                before.st_nlink != 1 or before.st_mode & 0o022 or not 0 < before.st_size <= maximum):
            raise ValueError('Protected recovery file required')
        data = os.read(leaf, maximum + 1)
        fields = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                                 value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(data) != before.st_size or any(fields(value) != fields(before) for value in
            (os.fstat(leaf), os.stat(filename.name, dir_fd=fd, follow_symlinks=False))):
            raise ValueError('Recovery source generation changed')
        directories()
        return data


class Receipts:
    def read(self):
        try:
            return json.loads(protected_read(STATE / 'receipt.json', 4096))
        except FileNotFoundError:
            return None

    def write(self, value, exclusive=False):
        info = STATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
            raise ValueError('Private recovery receipt directory required')
        # The supervised main/stop-post processes are serialized by systemd.
        target = STATE / ('receipt.json' if exclusive else '.pending-' + uuid.uuid4().hex)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            data = (json.dumps(value, sort_keys=True) + '\n').encode()
            while data:
                count = os.write(fd, data)
                if count <= 0:
                    raise ValueError('Receipt write incomplete')
                data = data[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if not exclusive:
            protected_read(STATE / 'receipt.json', 4096)
            os.replace(target, STATE / 'receipt.json')
        directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def command(args, timeout=85):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=True).stdout.decode().strip()


def unit(name):
    return dict(line.split('=', 1) for line in command(['/usr/bin/systemctl', 'show', name,
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID']).splitlines())


def quiescent(name):
    value = unit(name)
    if value['MainPID'] != '0' or value['ControlPID'] != '0' or value['ActiveState'] not in ('inactive', 'failed'):
        raise ValueError('Graph peer not quiescent')


def require_guard(state, birth):
    if (state['ActiveState'] != 'active' or not state['MainPID'].isdigit() or
            int(state['MainPID']) < 2 or state['ControlPID'] != '0' or not birth.isdigit()):
        raise ValueError('Original live recovery watcher required')
    return state['MainPID'], birth


def guard_identity():
    state = unit(GUARD)
    pid = state['MainPID']
    birth = Path('/proc/' + pid + '/stat').read_text().rpartition(') ')[2].split()[19]
    return require_guard(state, birth)


def expected_supervision():
    def invocation(filename, *args):
        return [['/usr/bin/python3', ['/usr/bin/python3', '-B', str(ROOT / filename), *args]]]
    return {'Type': 'exec', 'KillMode': 'control-group', 'Restart': 'no',
            'RuntimeMaxUSec': 180000000, 'TimeoutStopUSec': 60000000,
            'KillSignal': 9, 'FinalKillSignal': 9, 'SendSIGKILL': True, 'TimeoutStopFailureMode': 'kill',
            'ExecStart': invocation('graph-interruption-fixture.py'),
            'ExecStopPost': invocation('graph-recovery.py', '--recover-pending'),
            'DropInPaths': ['/run/systemd/system/' + UNIT + '.d/90-graph-recovery.conf']}


def validate_supervision(record):
    if record != expected_supervision():
        raise ValueError('Recovery worker supervision changed')


def supervision():
    import dbus
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                             'org.freedesktop.systemd1.Manager')
    obj = bus.get_object('org.freedesktop.systemd1', manager.LoadUnit(UNIT, timeout=5))
    properties = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
    service = properties.GetAll('org.freedesktop.systemd1.Service', timeout=5)
    record = {key: str(service[key]) for key in ('Type', 'KillMode', 'Restart')}
    record.update({key: int(service[key]) for key in ('RuntimeMaxUSec', 'TimeoutStopUSec')})
    record.update({key: int(service[key]) for key in ('KillSignal', 'FinalKillSignal')})
    record['SendSIGKILL'] = bool(service['SendSIGKILL'])
    record['TimeoutStopFailureMode'] = str(service['TimeoutStopFailureMode'])
    for key in ('ExecStart', 'ExecStopPost'):
        record[key] = []
        for item in service[key]:
            if bool(item[2]):
                raise ValueError('Recovery command ignores errors')
            record[key].append([str(item[0]), list(map(str, item[1]))])
    record['DropInPaths'] = list(map(str, properties.Get('org.freedesktop.systemd1.Unit', 'DropInPaths', timeout=5)))
    validate_supervision(record)


def switch_graph():
    command(['/usr/bin/systemctl', 'stop', *GRAPH], timeout=130)
    for name in GRAPH:
        quiescent(name)
    command(['/usr/bin/systemctl', 'start', COMPOSITOR])
    deadline = time.monotonic() + 10
    path = Path('/run/user/1000/wayland-teslaandroid')
    while True:
        try:
            before = path.lstat()
            if not stat.S_ISSOCK(before.st_mode) or before.st_uid != 1000:
                raise ValueError('Private compositor socket required')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(path))
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError('Compositor socket generation changed')
            break
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise ValueError('Compositor not ready')
            time.sleep(.1)
    compositor = unit(COMPOSITOR)
    command(['/usr/bin/systemctl', 'start', CONTAINER])
    import dbus
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object('id.waydro.Container', '/ContainerManager'), 'id.waydro.ContainerManager')
    if bool(manager.GetSession(timeout=5)):
        raise ValueError('Fresh root daemon already owns a session')
    command(['/usr/bin/systemctl', 'start', HEADLESS])
    if unit(COMPOSITOR)['MainPID'] != compositor['MainPID']:
        raise ValueError('Compositor restarted during attachment')


def context():
    source = Path('/opt/droiddeck/gpu-boot-20261015bq/gpu-boot.py')
    data = protected_read(source)
    if hashlib.sha256(data).hexdigest() != 'b158bb4971e29933e8738911c2b730286739b0e533e396e6538b7d092050758a':
        raise ValueError('Boot coordinator source changed')
    module = types.ModuleType('recovery_boot_coordination')
    module.__file__ = str(source)
    exec(compile(data, str(source), 'exec'), module.__dict__)
    coordinator = module.context()
    watcher = guard_identity()
    def admit():
        coordinator.admit()
        supervision()
        worker = Path('/etc/systemd/system/' + UNIT)
        if hashlib.sha256(protected_read(worker)).hexdigest() != '15c2c7b5688385716a50cdd1a75c5c24df38d9c6cfb025abdb99686e7fb82402':
            raise ValueError('Original supervised worker changed')
        if guard_identity() != watcher:
            raise ValueError('Recovery watcher generation changed')
        protected_read(Path(__file__))
        protected_read(ROOT / 'graph-interruption-fixture.py')
        quiescent('tesla-waydroid-runtime.service')
    def verify():
        if coordinator.store.status()['stage'] == 'starting':
            command(['/usr/bin/systemctl', 'start', READY], timeout=220)
        else:
            coordinator.verify()
    return Recovery(coordinator.store, Receipts(), boot=coordinator.boot,
                    admit=admit, switch=switch_graph, verify=verify)


def main(args):
    if (args not in (['--restart'], ['--recover-pending']) or sys.platform != 'linux' or os.geteuid() != 0 or
            Path(__file__) != ROOT / 'graph-recovery.py'):
        raise ValueError('Fixed protected root recovery required')
    state = unit(UNIT)
    field = 'MainPID' if args == ['--restart'] else 'ControlPID'
    if state[field] != str(os.getpid()):
        raise ValueError('Supervised recovery process required')
    recovery = context()
    getattr(recovery, 'restart' if args == ['--restart'] else 'recover_pending')()
    print('Owned graph recovery complete; native GPU retained; no healthy expiry')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Graph recovery rejected or incomplete', file=sys.stderr)
        sys.exit(1)
