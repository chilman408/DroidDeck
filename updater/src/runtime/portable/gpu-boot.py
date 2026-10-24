# SPDX-License-Identifier: GPL-3.0-only
"""Pinned disposable-VM boot coordination; never replay GPU activation/recovery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types

ROOT = Path('/opt/droiddeck/gpu-boot-20261015bq')
GPU_SOURCE = Path('/opt/droiddeck/gpu-recovery/runtime/portable/gpu-activation.py')
GUARD = 'droiddeck-gpu-recovery.service'
READY = 'droiddeck-gpu-boot-ready.service'
PEERS = (GUARD, 'waydroid-container.service', 'tesla-waydroid-compositor.service',
         'tesla-waydroid-headless-session.service')
PINS = {
    GPU_SOURCE: 'f2195b108f87d443d890bb26187da2aa4b4aa6fec37644d01c8a77788edcbd1a',
    Path('/var/lib/waydroid/waydroid.cfg'): '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8',
    Path('/etc/droiddeck/runtime.json'): '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3',
    Path('/opt/droiddeck/runtime/bundle-manifest.json'): '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71',
    Path('/etc/systemd/system/' + GUARD): 'fc443ac14fc3ec0e43b790271ec457e700bacda08b59f00b49f558b1881e071e',
    Path('/etc/systemd/system/tesla-waydroid-compositor.service'): '642d7eedda85a550a0a86d1222fe7718920f27f4191096bd8e642a65ce509119',
    Path('/etc/systemd/system/tesla-waydroid-headless-session.service'): 'b7efd8b4be74df80bd350396017d8137fe36dd62fb13f66a78023dfa169ca978',
    Path('/etc/systemd/system/tesla-waydroid-headless-session.service.d/60-droiddeck-compat.conf'): 'a2638975fe90c722b006c872f7411283d3603d4b0b798454af662a978f8dc7cf',
    Path('/opt/droiddeck/headless-compat-20261014bm/headless-session.py'): '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba',
    Path('/usr/lib/systemd/system/waydroid-container.service'): 'fbcde9a3afbec023aa7a92c601cf4b45e9905f7a3a43968b53a0e1d16b2820fb',
    Path('/etc/systemd/system/waydroid-container.service.d/40-droiddeck-pid-compat.conf'): '5d4468903cc8191b0487594eab755e441dd45e6ce1b4ff811ceff242b6a514fd',
}
OWN_PINS = {
    Path('/etc/systemd/system/' + READY): 'a75c0b76e3a493cb8916938652e1f8ed3a75b5f13c5927910cbc1e17fb2daf0a',
    Path('/etc/systemd/system/' + GUARD + '.d/70-droiddeck-boot.conf'): 'b43c12757d06896e4056819c3157e2a1e2c977bea03753d2fc3ede60bfeeeee9',
    Path('/etc/systemd/system/tesla-waydroid-compositor.service.d/70-droiddeck-boot.conf'): 'b27a4edd2daba5c515963d8a7795a9c970456c7706255a06d4a2d8a9ad602045',
    Path('/etc/systemd/system/waydroid-container.service.d/70-droiddeck-boot.conf'): 'b27a4edd2daba5c515963d8a7795a9c970456c7706255a06d4a2d8a9ad602045',
}


def admit_owned(read):
    for filename, digest in OWN_PINS.items():
        if hashlib.sha256(read(filename).replace(b'\r\n', b'\n')).hexdigest() != digest:
            raise ValueError('Owned boot dependency source changed')


class Coordinator:
    def __init__(self, store, *, boot, admit, peers, native, clock=time.monotonic, sleep=time.sleep):
        self.store, self.boot, self.admit = store, boot, admit
        self.peers, self.native, self.clock, self.sleep = peers, native, clock, sleep

    def arm(self):
        self.admit()
        with self.store.lock():
            self.admit()
            state = self.store.status()
            if state['boot'] == self.boot() and state['stage'] in ('ready', 'starting'):
                return  # Preserve the original deadline even if guard restarts.
            if state['stage'] != 'ready' or state['boot'] == self.boot():
                return  # Let the ORIGINAL watcher handle interrupted/restored states.
            self.store.stage('starting')

    def verify(self):
        deadline = self.clock() + 150
        while self.clock() < deadline:
            self.admit()
            with self.store.lock():
                before = self.store.status()
                if before['boot'] != self.boot() or before['stage'] not in ('starting', 'ready'):
                    raise ValueError('No current boot admission')
            identity = self.peers()
            try:
                healthy = self.native() is True
            except (OSError, ValueError, subprocess.SubprocessError):
                healthy = False
            self.admit()
            if self.peers() != identity:
                raise ValueError('Startup peer generation changed')
            with self.store.lock():
                if self.store.status() != before:
                    raise ValueError('Recovery changed during native verification')
                if healthy:
                    if before['stage'] == 'starting':
                        self.store.stage('ready')
                    return True
            self.sleep(2)
        raise ValueError('Native boot health did not verify')


def require_supervision(state, action, pid):
    field = 'ControlPID' if action == 'arm' else 'MainPID'
    other = 'MainPID' if action == 'arm' else 'ControlPID'
    if action not in ('arm', 'verify') or state.get('ActiveState') != 'activating' or state.get(field) != str(pid) or state.get(other) != '0':
        raise ValueError('Exact supervised startup process required')


def arm_guard(factory):
    try:
        factory().arm()
        return True
    except Exception:
        # An added ExecStartPre must never prevent original recovery from running.
        return False


def expected_settings():
    def command_record(*args):
        return [[args[0], list(args)]]
    node = '/opt/droiddeck/runtime/portable/worker-entry.js'
    prefix = '/etc/systemd/system/'
    def record(kind, start, pre, dropins, notify='none'):
        return {'Type': kind, 'NotifyAccess': notify, 'ExecStart': start,
                'ExecStartPre': pre, 'DropInPaths': dropins}
    return {
        GUARD: record('simple', command_record('/usr/bin/python3', str(GPU_SOURCE), '--watch'),
            command_record('/usr/bin/python3', '-B', str(ROOT / 'gpu-boot.py'), '--arm'),
            [prefix + GUARD + '.d/70-droiddeck-boot.conf']),
        'waydroid-container.service': record('dbus', command_record('/usr/bin/waydroid', 'container', 'start'),
            command_record('/usr/bin/node', '/opt/droiddeck/pid-compat/portable/pid-startup.js'),
            [prefix + 'waydroid-container.service.d/40-droiddeck-pid-compat.conf',
             prefix + 'waydroid-container.service.d/70-droiddeck-boot.conf']),
        'tesla-waydroid-compositor.service': record('simple', command_record('/usr/bin/weston',
            '--backend=pipewire', '--renderer=gl', '--socket=wayland-teslaandroid', '--width=1280',
            '--height=720', '--idle-time=0', '--no-config', '--shell=kiosk-shell.so'),
            command_record('/usr/bin/node', node, 'compositor-check'),
            [prefix + 'tesla-waydroid-compositor.service.d/70-droiddeck-boot.conf']),
        'tesla-waydroid-headless-session.service': record('notify', command_record('/usr/bin/python3', '-B',
            '/opt/droiddeck/headless-compat-20261014bm/headless-session.py'),
            command_record('/usr/bin/node', node, 'headless-check'),
            [prefix + 'tesla-waydroid-headless-session.service.d/60-droiddeck-compat.conf'], 'main'),
        READY: record('oneshot', command_record('/usr/bin/python3', '-B', str(ROOT / 'gpu-boot.py'), '--verify'), [], []),
    }


def validate_settings(records):
    if records != expected_settings():
        raise ValueError('Effective boot service settings changed')


def effective_settings():
    import dbus
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                             'org.freedesktop.systemd1.Manager')
    result = {}
    for name in expected_settings():
        # Loading unit metadata does not start it; GetUnit loses inactive units
        # to systemd garbage collection before the first enabled boot.
        obj = bus.get_object('org.freedesktop.systemd1', manager.LoadUnit(name, timeout=5))
        properties = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
        service = properties.GetAll('org.freedesktop.systemd1.Service', timeout=5)
        unit_properties = properties.GetAll('org.freedesktop.systemd1.Unit', timeout=5)
        record = {key: str(service[key]) for key in ('Type', 'NotifyAccess')}
        for key in ('ExecStart', 'ExecStartPre'):
            record[key] = []
            for item in service[key]:
                if bool(item[2]):
                    raise ValueError('Boot command ignores errors')
                record[key].append([str(item[0]), list(map(str, item[1]))])
        record['DropInPaths'] = list(map(str, unit_properties['DropInPaths']))
        result[name] = record
    return result


def command(args, timeout=10):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=timeout).stdout.decode().strip()


def unit(name):
    return dict(line.split('=', 1) for line in command(['/usr/bin/systemctl', 'show', name,
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID']).splitlines())


def context():
    # Reuse the pinned protected reader and Store only. No activate/recover calls.
    transition = Path('/opt/droiddeck/headless-compat-20261014bm/headless-transition.py')
    info = transition.lstat()
    if not transition.is_file() or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
        raise ValueError('Protected coordination source required')
    data = transition.read_bytes()
    if hashlib.sha256(data).hexdigest() != '2c7759a6a0203416ce8eb5ad024aecafd9f898c5923488e5de37adadf7631f8a':
        raise ValueError('Immutable coordination source changed')
    module = types.ModuleType('gpu_boot_coordination')
    module.__file__ = str(transition)
    exec(compile(data, str(transition), 'exec'), module.__dict__)
    def admit():
        for filename, digest in PINS.items():
            if module.digest(filename) != digest:
                raise ValueError('Accepted boot baseline changed')
        module.read(Path(__file__))
        admit_owned(module.read)
        validate_settings(effective_settings())
    admit()
    store = module.gpu_store()
    def peers():
        values = []
        for name in PEERS:
            state = unit(name)
            if state['ActiveState'] != 'active' or int(state['MainPID']) < 2 or state['ControlPID'] != '0':
                raise ValueError('Boot peer not ready')
            pid = state['MainPID']
            stat = Path('/proc/' + pid + '/stat').read_text().rpartition(') ')[2].split()
            values.append((pid, stat[19]))
        return tuple(values)
    return Coordinator(store, boot=lambda: Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       admit=admit, peers=peers, native=module.native)


def main(args):
    if args not in (['--arm'], ['--verify'], ['--inspect']) or sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Fixed root Linux boot command required')
    if Path(__file__) != ROOT / 'gpu-boot.py':
        raise ValueError('Protected boot component installation required')
    action = args[0][2:]
    if action != 'inspect':
        require_supervision(unit(GUARD if action == 'arm' else READY), action, os.getpid())
    if action == 'arm':
        armed = arm_guard(context)
        print('GPU boot arm checked' if armed else 'GPU boot coordination deferred; original recovery retains control')
        return
    coordinator = context()
    if action == 'inspect':
        with coordinator.store.lock():
            state = coordinator.store.status()
        print(json.dumps({'kind': 'gpu-boot-coordination', 'stage': state['stage'],
            'currentBoot': state['boot'] == coordinator.boot(), 'installable': False,
            'rebootVerified': False}))
    else:
        getattr(coordinator, action)()
        print('GPU boot ' + action + ' complete; original recovery retained; no healthy expiry')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('GPU boot coordination rejected or incomplete', file=sys.stderr)
        sys.exit(1)
