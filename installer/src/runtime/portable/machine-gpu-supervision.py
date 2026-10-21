# SPDX-License-Identifier: GPL-3.0-only
"""Peer lifetime guards and single-use watcher readiness; no production entry."""
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import sys

WATCHER = 'droiddeck-machine-gpu-watcher.service'
WORKER = 'droiddeck-machine-gpu-worker.service'
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'InvocationID', 'ControlGroup')
NOTIFY_SOCKET = Path('/run/systemd/notify')


def unit(name):
    if name not in (WATCHER, WORKER):
        raise ValueError('Closed supervision peer required')
    result = subprocess.run(['/usr/bin/systemctl', 'show', name, *[arg for key in FIELDS for arg in ('-p', key)]],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError('Supervision peer status unavailable')
    lines = result.stdout.decode('ascii').splitlines()
    pairs = [line.split('=', 1) for line in lines]
    if len(pairs) != len(FIELDS) or any(len(pair) != 2 for pair in pairs) or {pair[0] for pair in pairs} != set(FIELDS):
        raise ValueError('Closed supervision status required')
    return dict(pairs)


def _read(directory, name, maximum):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        raw = os.read(fd, maximum + 1)
        if not 0 < len(raw) <= maximum:
            raise ValueError('Bounded process observation required')
        return raw
    finally:
        os.close(fd)


def process(pid, cgroup):
    if type(pid) is not int or not 1 < pid < 2**31 or cgroup not in ('/system.slice/' + WATCHER, '/system.slice/' + WORKER):
        raise ValueError('Closed peer process required')
    directory = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        def birth():
            raw = _read(directory, 'stat', 4096)
            prefix, separator, suffix = raw.rpartition(b') ')
            fields = suffix.split()
            if (not separator or not prefix.startswith(str(pid).encode() + b' (') or len(fields) < 20
                    or fields[0] in (b'Z', b'X', b'x') or not fields[19].isdigit()
                    or not 0 < int(fields[19]) < 2**63):
                raise ValueError('Live peer birth required')
            return int(fields[19])
        original = birth()
        status = _read(directory, 'status', 65536).splitlines()
        for key in (b'Uid:', b'Gid:'):
            rows = [row.split()[1:] for row in status if row.startswith(key)]
            if rows != [[b'0', b'0', b'0', b'0']]:
                raise ValueError('Root peer credentials required')
        if _read(directory, 'cgroup', 4096) != ('0::' + cgroup + '\n').encode('ascii') or birth() != original:
            raise ValueError('Peer process generation or cgroup changed')
        return original
    finally:
        os.close(directory)


def boot():
    raw = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii')
    if not re.fullmatch('[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\n', raw):
        raise ValueError('Canonical current boot required')
    return raw[:-1]


def _peer(value, name):
    if (type(value) is not dict or set(value) != set(FIELDS) or any(type(v) is not str for v in value.values())
            or any(value[k] != expected for k, expected in
                (('LoadState', 'loaded'), ('ActiveState', 'active'), ('SubState', 'running'), ('ControlPID', '0'),
                 ('ControlGroup', '/system.slice/' + name)))
            or not re.fullmatch('[1-9][0-9]{0,9}', value['MainPID']) or not 1 < int(value['MainPID']) < 2**31
            or not re.fullmatch('[a-f0-9]{32}', value['InvocationID']) or value['InvocationID'] == '0' * 32):
        raise ValueError('Exact live supervision peer required')
    return dict(value)


def hold_worker_supervision(stack, *, admit=None, observe=None, identify=None, current_boot=None):
    # admit must retain/check the independent installed-source and effective-graph proof.
    # This lifetime layer does not implement or claim either proof on its own.
    if sys.platform != 'linux' or os.geteuid() != 0 or not callable(admit):
        raise ValueError('Root Linux independent recipe admission required')
    observe = unit if observe is None else observe
    identify = process if identify is None else identify
    current_boot = boot if current_boot is None else current_boot
    owner = os.getpid()

    def sample():
        if os.getpid() != owner:
            raise ValueError('Supervision guard cannot be inherited by another process')
        admit()
        boot_id = current_boot()
        if type(boot_id) is not str or not re.fullmatch('[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', boot_id):
            raise ValueError('Bounded boot identity required')
        peers = {name: _peer(observe(name), name) for name in (WATCHER, WORKER)}
        if int(peers[WORKER]['MainPID']) != owner or int(peers[WATCHER]['MainPID']) == owner:
            raise ValueError('Separate watcher and exact current worker required')
        births = {name: identify(int(record['MainPID']), record['ControlGroup']) for name, record in peers.items()}
        if any(type(value) is not int or not 0 < value < 2**63 for value in births.values()):
            raise ValueError('Bounded peer process birth required')
        if current_boot() != boot_id or any(_peer(observe(name), name) != peers[name] for name in peers):
            raise ValueError('Supervision peers changed during observation')
        admit()
        return boot_id, peers, births

    original = sample()
    poller = select.poll()
    for record in original[1].values():
        fd = os.pidfd_open(int(record['MainPID']), 0)
        stack.callback(os.close, fd)
        poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)

    def check():
        if poller.poll(0):
            raise ValueError('Original supervision process exited')
        if sample() != original or poller.poll(0):
            raise ValueError('Original supervision generation changed')
    check()
    return {'check': check, 'peerLifetimeGuardHeld': True,
            'installedRecipeVerified': False, 'effectiveGraphVerified': False,
            'independentRecoveryArmed': False, 'servicesActivated': False,
            'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}


class WatcherReadinessUncertain(RuntimeError):
    """The readiness packet may have been delivered; never send it again automatically."""


def _starting_watcher(value, *, after_send=False):
    if type(value) is not dict:
        raise ValueError('Closed starting watcher observation required')
    phase = value.get('ActiveState'), value.get('SubState')
    if phase != ('activating', 'start') and not (after_send and phase == ('active', 'running')):
        raise ValueError('Exact starting watcher, or post-notification running watcher required')
    _peer({**value, 'ActiveState': 'active', 'SubState': 'running'}, WATCHER)
    return dict(value)


def _hold_notify_endpoint(stack):
    chain, current = [], None
    for part in NOTIFY_SOCKET.parent.parts:
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        original = info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
        chain.append((current, part, fd, original))
        current = fd
    endpoint = os.open(NOTIFY_SOCKET.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=current)
    stack.callback(os.close, endpoint)
    info = os.fstat(endpoint)
    original = info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink

    def check():
        for parent, name, fd, expected in chain:
            for value in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if (not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022
                        or (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid) != expected):
                    raise ValueError('Original protected systemd notification namespace required')
        for value in (os.fstat(endpoint), os.stat(NOTIFY_SOCKET.name, dir_fd=current, follow_symlinks=False)):
            if (not stat.S_ISSOCK(value.st_mode) or value.st_uid != 0 or value.st_gid != 0 or value.st_nlink != 1
                    or (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid, value.st_nlink) != original):
                raise ValueError('Original root-owned notification socket required')
    check()
    return check


def hold_watcher_readiness(stack, *, admit=None, observe=None, identify=None, current_boot=None):
    """Real main-process READY transport; caller must independently admit sources/graph/state."""
    if sys.platform != 'linux' or os.geteuid() != 0 or not callable(admit):
        raise ValueError('Root Linux independent watcher admission required')
    observe = unit if observe is None else observe
    identify = process if identify is None else identify
    current_boot = boot if current_boot is None else current_boot
    owner = os.getpid()
    environment = os.environ.get('NOTIFY_SOCKET'), os.environ.get('INVOCATION_ID')
    if (environment[0] != str(NOTIFY_SOCKET) or type(environment[1]) is not str
            or not re.fullmatch('[a-f0-9]{32}', environment[1]) or environment[1] == '0' * 32):
        raise ValueError('Exact systemd watcher notification environment required')
    attempted, active_seen = False, False

    def sample():
        nonlocal active_seen
        if sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != owner:
            raise ValueError('Original root watcher process required')
        if (os.environ.get('NOTIFY_SOCKET'), os.environ.get('INVOCATION_ID')) != environment:
            raise ValueError('Original watcher notification environment required')
        admit()
        boot_id = current_boot()
        if type(boot_id) is not str or not re.fullmatch('[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', boot_id):
            raise ValueError('Canonical watcher notification boot required')
        record = _starting_watcher(observe(WATCHER), after_send=attempted)
        if int(record['MainPID']) != owner or record['InvocationID'] != environment[1]:
            raise ValueError('Exact current watcher main process and invocation required')
        birth = identify(owner, record['ControlGroup'])
        if type(birth) is not int or not 0 < birth < 2**63:
            raise ValueError('Bounded original watcher birth required')
        after = _starting_watcher(observe(WATCHER), after_send=attempted)
        def identity(value):
            return {k: v for k, v in value.items() if k not in ('ActiveState', 'SubState')}
        forward = attempted and record['ActiveState'] == 'activating' and after['ActiveState'] == 'active'
        if identity(after) != identity(record) or (after != record and not forward) or current_boot() != boot_id:
            raise ValueError('Watcher changed during notification admission')
        running = after['ActiveState'] == 'active'
        if active_seen and not running:
            raise ValueError('Watcher readiness phase regressed')
        active_seen = active_seen or running
        admit()
        return boot_id, identity(record), birth

    original = sample()
    pidfd = os.pidfd_open(owner, 0)
    stack.callback(os.close, pidfd)
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLERR | select.POLLHUP)
    endpoint = _hold_notify_endpoint(stack)

    def check():
        if poller.poll(0):
            raise ValueError('Original watcher process exited')
        endpoint()
        if sample() != original or poller.poll(0):
            raise ValueError('Original watcher notification generation changed')
        endpoint()

    check()
    sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    stack.callback(sender.close)
    sender.settimeout(1.0)
    sender.connect(str(NOTIFY_SOCKET))
    check()
    false = dict.fromkeys(('installedRecipeVerified', 'effectiveGraphVerified', 'independentRecoveryArmed',
        'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'), False)

    def notify():
        nonlocal attempted
        if attempted:
            raise ValueError('Watcher readiness notification is single-use')
        check()
        attempted = True
        try:
            # Direct send preserves the actual main PID; no helper/subprocess identity.
            if sender.send(b'READY=1') != 7:
                raise ValueError('Exact readiness datagram required')
            check()
        except (OSError, ValueError, RuntimeError) as error:
            raise WatcherReadinessUncertain('Watcher readiness may have been delivered; inspect state, do not retry') from error
        return {'readinessDatagramSent': True, **false}

    return {'check': check, 'notify': notify, 'readinessTransportHeld': True, **false}
