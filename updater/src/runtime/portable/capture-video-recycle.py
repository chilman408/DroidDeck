# SPDX-License-Identifier: GPL-3.0-only
"""Experimental admitted video-only auto-restart; no CLI or policy/release writer.

Caller must hold the independent serialized operation/source/config admission and
supply bounded readiness checks. This library is not that admission authority.
No systemctl restart/stop, cgroup sweep, healthy expiry or automatic retry.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

VIDEO = 'tesla-waydroid-video-preview.service'
GUARDS = tuple('tesla-waydroid-' + n + '.service' for n in
               ('runtime', 'compositor', 'audio-preview', 'gateway-preview', 'audio-relay-preview', 'video-relay-preview'))
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'InvocationID',
          'ControlGroup', 'NRestarts', 'ExecMainStartTimestampMonotonic', 'Restart',
          'RestartUSec', 'KillMode', 'FragmentPath', 'DropInPaths')
OWNED_DROP='/run/systemd/system/'+VIDEO+'.d/90-droiddeck-capture-view.conf'
STABLE = ('LoadState', 'Restart', 'RestartUSec', 'KillMode', 'FragmentPath', 'DropInPaths')
BOOT = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'


def number(value, *, positive=False):
    if type(value) is not str or not re.fullmatch('0|[1-9][0-9]{0,18}', value):
        raise ValueError('Canonical service counter required')
    result = int(value)
    if result >= 2**63 or positive and not result:
        raise ValueError('Bounded service counter required')
    return result


def record(value, unit, *, running=True):
    if type(value) is not dict or set(value) != set(FIELDS) or any(type(v) is not str for v in value.values()):
        raise ValueError('Closed systemd observation required')
    if (value['LoadState'] != 'loaded' or value['ControlGroup'] != '/system.slice/' + unit
            or value['ControlPID'] != '0' or not re.fullmatch('[a-f0-9]{32}', value['InvocationID'])
            or value['InvocationID'] == '0' * 32):
        raise ValueError('Exact supervised service required')
    number(value['NRestarts'])
    number(value['MainPID'])
    number(value['ExecMainStartTimestampMonotonic'])
    if running and (value['ActiveState'] != 'active' or value['SubState'] != 'running'
                    or not 1 < number(value['MainPID']) < 2**31
                    or not number(value['ExecMainStartTimestampMonotonic'], positive=True)):
        raise ValueError('Active supervised process required')
    return dict(value)


def stable(value):
    return {key: value[key] for key in STABLE}


class Backend:
    # Fixture tests subclass this closed backend, never add a production unit argument.
    video_unit = VIDEO

    def observe(self, unit):
        if unit not in (self.video_unit, *GUARDS):
            raise ValueError('Closed video or guarded peer required')
        q = subprocess.run(['/usr/bin/systemctl', 'show', unit, *[a for k in FIELDS for a in ('-p', k)]],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=2,
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        if q.returncode or len(q.stdout) > 8192:
            raise ValueError('Bounded systemd observation unavailable')
        try:
            pairs = [line.split('=', 1) for line in q.stdout.decode('ascii').splitlines()]
            if len(pairs) != len(FIELDS) or any(len(p) != 2 for p in pairs) or {p[0] for p in pairs} != set(FIELDS):
                raise ValueError()
            return dict(pairs)
        except (ValueError, UnicodeError):
            raise ValueError('Closed systemd observation unavailable') from None

    def boot(self):
        value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii')
        if not re.fullmatch(BOOT + r'\n', value):
            raise ValueError('Canonical boot required')
        return value[:-1]

    def process(self, value):
        pid = number(value['MainPID'], positive=True)
        if not 1 < pid < 2**31 or value['ControlGroup'] != '/system.slice/' + self.video_unit:
            raise ValueError('Exact video process required')
        directory = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            def read(name, limit):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    raw = os.read(fd, limit + 1)
                    if not 0 < len(raw) <= limit: raise ValueError('Bounded video observation required')
                    return raw
                finally: os.close(fd)
            def birth():
                raw = read('stat', 4096)
                head, sep, tail = raw.rpartition(b') ')
                fields = tail.split()
                if (not sep or not head.startswith(str(pid).encode() + b' (') or len(fields) < 20
                        or fields[0] in (b'Z', b'X', b'x') or not fields[19].isdigit()
                        or not 0 < int(fields[19]) < 2**63):
                    raise ValueError('Live video process birth required')
                return int(fields[19])
            original = birth()
            status = read('status', 65536).splitlines()
            for key in (b'Uid:', b'Gid:'):
                if [v.split()[1:] for v in status if v.startswith(key)] != [[b'0'] * 4]:
                    raise ValueError('Root supervised launcher required')
            if read('cgroup', 4096) != ('0::' + value['ControlGroup'] + '\n').encode() or birth() != original:
                raise ValueError('Video process identity changed')
            return original
        finally: os.close(directory)

    def handle(self, value): return os.pidfd_open(number(value['MainPID'], positive=True), 0)
    def exited(self, fd):
        poll = select.poll()
        poll.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
        return bool(poll.poll(0))
    def terminate(self, fd): signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
    def close(self, fd): os.close(fd)
    def now(self): return time.monotonic()
    def sleep(self): time.sleep(.1)


class Recycle:
    def __init__(self, admit, backend, owned_graph=None):
        if sys.platform != 'linux' or os.geteuid() != 0 or not callable(admit):
            raise ValueError('Root Linux independent operation admission required')
        self.admit, self.b, self.pidfd = admit, backend, None
        if owned_graph is not None and not callable(owned_graph):raise ValueError("Exact owned graph admission required")
        self.owned_graph=owned_graph
        self.owner, self.used = os.getpid(), False
        self.before, self.peers, self.boot = self._sample()
        if (self.before['Restart'] != 'always' or self.before['RestartUSec'] != '2s'
                or self.before['KillMode'] != 'control-group'
                or self.before['FragmentPath'] != '/run/systemd/transient/' + self.b.video_unit
                or self.before['DropInPaths'] not in ('',OWNED_DROP)
                or self.before['DropInPaths'] and self.owned_graph is None):
            raise ValueError('Admitted supervised video auto-restart policy required')
        self.birth = self.b.process(self.before)
        try:
            self.pidfd = self.b.handle(self.before)
            self._original()
        except BaseException:
            self.close()
            raise

    def _admit(self):
        if os.getpid() != self.owner or self.admit() is not True:
            raise ValueError('Independent serialized source/config admission lost')
        if self.owned_graph is not None and self.owned_graph() is not True:
            raise ValueError('Exact independently owned effective graph admission lost')

    def _sample(self):
        self._admit()
        boot = self.b.boot()
        if type(boot) is not str or not re.fullmatch(BOOT, boot): raise ValueError('Boot identity required')
        peers = {unit: record(self.b.observe(unit), unit) for unit in GUARDS}
        video = record(self.b.observe(self.b.video_unit), self.b.video_unit)
        if boot != self.b.boot() or any(self.b.observe(unit) != value for unit, value in peers.items()) or self.b.observe(self.b.video_unit) != video:
            raise ValueError('Service generation changed during admission')
        self._admit()
        return video, peers, boot

    def _guards(self):
        self._admit()
        if self.b.boot() != self.boot or any(self.b.observe(unit) != value for unit, value in self.peers.items()):
            raise ValueError('Guarded service or boot changed')

    def _original(self):
        self._guards()
        if (self.pidfd is None or self.b.exited(self.pidfd) or self.b.observe(self.b.video_unit) != self.before
                or self.b.process(self.before) != self.birth or self.b.exited(self.pidfd)):
            raise ValueError('Original video generation no longer held')
        self._guards()

    def run(self, ready):
        if self.used or not callable(ready): raise ValueError('Single-use recycle and bounded readiness required')
        self._original()
        self.used = True  # A possibly delivered signal must never be replayed.
        self.b.terminate(self.pidfd)
        deadline = self.b.now() + (30 if self.owned_graph is not None else 9)
        attempts = 0
        while self.b.now() < deadline and attempts < (300 if self.owned_graph is not None else 100):
            attempts += 1
            self._guards()
            current = self.b.observe(self.b.video_unit)
            if stable(current) != stable(self.before):
                raise ValueError('Video unit configuration changed during recycle')
            # systemd may remove the now-empty cgroup during auto-restart wait.
            # Only the zero-main-PID waiting state may lack it; fresh admission
            # still requires the exact unit cgroup and root process identity.
            expected_group = self.before['ControlGroup']
            if current['ControlGroup'] != expected_group and not (
                    current['ControlGroup'] == '' and current['ActiveState'] == 'activating'
                    and current['SubState'] == 'auto-restart' and current['MainPID'] == '0'):
                raise ValueError('Video left its expected cgroup')
            restarts = number(current['NRestarts'])
            if restarts not in (number(self.before['NRestarts']), number(self.before['NRestarts']) + 1):
                raise ValueError('Video restart storm or counter regression')
            if current['ActiveState'] == 'active' and current['SubState'] == 'running' and current['MainPID'] != self.before['MainPID']:
                record(current, self.b.video_unit)
                if (not self.b.exited(self.pidfd) or current['InvocationID'] == self.before['InvocationID']
                        or restarts != number(self.before['NRestarts']) + 1
                        or number(current['ExecMainStartTimestampMonotonic']) <= number(self.before['ExecMainStartTimestampMonotonic'])):
                    raise ValueError('Fresh supervised video invocation required')
                birth = self.b.process(current)
                if ready(dict(current)) is True:
                    self._guards()
                    if (self.b.observe(self.b.video_unit) != current or self.b.process(current) != birth
                            or self.b.now() >= deadline):
                        raise ValueError('Ready video generation changed')
                    return {'before': dict(self.before), 'after': current, 'bootId': self.boot,
                            'videoOnly': True, 'guardedPeersUnchanged': True, 'ready': True,
                            'installedRecipeVerified': False, 'releaseAdmissionVerified': False}
            elif current['ActiveState'] not in ('active', 'activating'):
                raise ValueError('Video left supported auto-restart path')
            self.b.sleep()
        raise TimeoutError('Video auto-restart/readiness deadline exceeded')

    def close(self):
        if self.pidfd is not None:
            fd, self.pidfd = self.pidfd, None
            self.b.close(fd)


@contextmanager
def hold_video_recycle(*, admit, owned_graph=None, _backend=None):
    value = Recycle(admit, Backend() if _backend is None else _backend,owned_graph)
    try: yield value
    finally: value.close()
