# SPDX-License-Identifier: GPL-3.0-only
"""Disposable v3 acceptance: observe real rollback exchange and kill its pidfd.

No arbitrary PID, unit, code destination or source can be supplied. This observer
never requests rollback or changes Android state; the separate installer does so.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import struct
import sys
import time

SOURCE = Path('/var/lib/droiddeck/gateway-update-controller-v3/sources/runtime/portable/gateway-update-chain.py')
SOURCE_PIN = 'c0f444355b05b2fac7d9a97db474245e75fe1dd2f407f5e7e36ef2123198ed5a'
PROC = Path('/proc')
MOVED_TO = 0x80


def owner_uid(path): return path.lstat().st_uid


def controller_pid(c, tx):
    state = tx.g.unit(tx.unit)
    if state['ActiveState'] != 'active' or int(state['MainPID']) < 2 or state['ControlPID'] != '0':
        raise ValueError('Original running rollback controller required')
    raw = tx.g.command(['/usr/bin/systemctl', 'show', tx.unit, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
    if dict(line.split('=', 1) for line in raw.decode('ascii').splitlines()) != {
            'FragmentPath': str(c.B.SYSTEMD / c.B.TEMPLATE), 'DropInPaths': ''}:
        raise ValueError('Original controller template without overrides required')
    pid = int(state['MainPID']); process = PROC / str(pid)
    if owner_uid(process) != 0 or (process / 'cmdline').read_bytes().split(b'\0') != [
            b'/usr/bin/python3', b'-I', b'-B', str(SOURCE).encode('ascii'), b'run', tx.digest.encode('ascii'), b'']:
        raise ValueError('Exact root controller command and original PID required')
    return pid, state


def exchange_events(raw, name):
    offset, count = 0, 0
    while offset < len(raw):
        if len(raw) - offset < 16: raise ValueError('Truncated exchange observation')
        _watch, mask, _cookie, size = struct.unpack_from('iIII', raw, offset)
        if not 0 < size <= 4096 or offset + 16 + size > len(raw): raise ValueError('Bounded complete event required')
        value = raw[offset + 16:offset + 16 + size].split(b'\0', 1)[0]
        offset += 16 + size
        if mask & MOVED_TO and value == name.encode('ascii'): count += 1
    return count


def native(c):
    result = c.G.command(['/usr/bin/node', str(c.B.SOURCE / 'runtime/portable/gpu-native.js')], timeout=35)
    value = json.loads(result.stdout)
    if value.get('nativeGpuVerified') is not True: raise ValueError('Original native GPU must be healthy')
    return {name: value[name] for name in ('containerIdentity', 'androidIdentity')}


def trial(c, digest):
    if c.P.sha(c.G.protected_read(SOURCE)) != SOURCE_PIN: raise ValueError('Exact accepted v3 source required')
    c.B.inspect_bootstrap(c.inspect_controller())
    current = c.current_view(c.records())
    if current is None or current.digest != c.P.digest(digest): raise ValueError('Original current release required')
    side, phases = current.observe()
    if side != 'after' or 'healthy.json' not in phases or 'rollback-request.json' in phases:
        raise ValueError('Observe only the original healthy release before requesting rollback')
    before = native(c); watcher = current.g.unit(current.g.WATCHER)
    if watcher['ActiveState'] != 'active' or int(watcher['MainPID']) < 2:
        raise ValueError('Original independent GPU watcher required')
    libc = ctypes.CDLL(None, use_errno=True)
    events = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    if events < 0: raise OSError(ctypes.get_errno(), 'Cannot observe original code exchange')
    pidfd = None
    try:
        if libc.inotify_add_watch(events, os.fsencode(c.B.LIVE.parent), MOVED_TO) < 0:
            raise OSError(ctypes.get_errno(), 'Cannot observe fixed live gateway parent')
        print(json.dumps({'phase': 'watching-original-rollback-exchange', 'digest': digest}), flush=True)
        deadline = time.monotonic() + 600; killed, replacement = None, None
        while time.monotonic() < deadline and killed is None:
            if not select.select([events], [], [], 1)[0]: continue
            if not exchange_events(os.read(events, 65536), c.B.LIVE.name): continue
            tx = c.Transaction(digest); side, phases = tx.observe()
            if (side != 'before' or not {'rollback-request.json', 'rollback-intent.json'} <= phases
                    or 'rolled-back.json' in phases):
                raise ValueError('Original pending restored rollback required; no signal sent')
            pid, state = controller_pid(c, tx); pidfd = os.pidfd_open(pid)
            # Retained pidfd prevents PID reuse. Revalidate original process,
            # systemd identity and phase immediately before the one signal.
            if controller_pid(c, tx) != (pid, state) or tx.g.unit(tx.g.WATCHER) != watcher:
                raise ValueError('Original controller and independent watcher changed; no signal sent')
            if 'rolled-back.json' in tx.observe()[1]: raise ValueError('Rollback already complete; no signal sent')
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            killed = pid
            print(json.dumps({'phase': 'controller-killed-after-original-code-restored', 'digest': digest,
                'oldControllerPid': pid, 'originalGpuWatcherPid': watcher['MainPID']}), flush=True)
        if killed is None: raise TimeoutError('No original rollback exchange observed; no process killed')
        while time.monotonic() < deadline:
            state = current.g.unit(current.unit)
            if state['ActiveState'] == 'active' and int(state['MainPID']) > 1 and int(state['MainPID']) != killed:
                replacement = int(state['MainPID'])
            if (current.directory / 'rolled-back.json').exists():
                tx = c.Transaction(digest); side, phases = tx.observe()
                if (not replacement or side != 'before' or 'rolled-back.json' not in phases
                        or current.g.unit(current.g.WATCHER) != watcher or native(c) != before):
                    raise ValueError('Independent original rollback and unchanged native generation required')
                result = {'kind': 'live-cumulative-controller-interruption-verified', 'digest': digest,
                    'oldControllerPid': killed, 'restartedControllerPid': replacement,
                    'originalDirectoryInodeRestored': True, 'automaticOriginalInstallationProofCompleted': True,
                    'androidGenerationUnchanged': True, 'originalGpuWatcherUnchanged': True,
                    'manualFinalizerUsed': False, 'screenshotsSaved': False, 'mediaSaved': False}
                print(json.dumps(result, sort_keys=True), flush=True)
                return result
            time.sleep(1)
        raise TimeoutError('Automatic rollback incomplete; preserve original journals')
    finally:
        if pidfd is not None: os.close(pidfd)
        os.close(events)


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or len(sys.argv) != 2:
        raise ValueError('Explicit root Linux disposable-controller test and exact digest required')
    spec = importlib.util.spec_from_file_location('accepted_chain', SOURCE)
    c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
    trial(c, c.P.digest(sys.argv[1]))


if __name__ == '__main__': main()
