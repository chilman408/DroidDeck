# SPDX-License-Identifier: GPL-3.0-only
"""Actual .7 no-code handoff interruption, using the enrolled guard's public API.

No fixture markers or production fault hooks. Observe a real fsynced grant,
confirm the original watcher stopped, kill ONLY the pidfd-bound guard process,
then require its independent systemd restart and original-owner recovery.
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

ENTRY = Path('/var/lib/droiddeck/update-supervision-v2/sources/runtime/portable/update-guard.py')
CREATE, CLOSE_WRITE = 0x100, 0x8


def main():
    if sys.argv[1:] or sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Explicit root Linux enrolled no-code handoff test only')
    spec = importlib.util.spec_from_file_location('enrolled_proof_guard', ENTRY)
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    context = d.inspect()
    module, _active = d.activation(context)
    g = module.G
    original = g.unit(d.GUARD)
    if original['ActiveState'] != 'active' or int(original['MainPID']) < 2 or original['ControlPID'] != '0':
        raise ValueError('Original independent active guard required')
    runtime = g.unit('tesla-waydroid-runtime.service')
    if runtime['ActiveState'] != 'active' or int(runtime['MainPID']) < 2:
        raise ValueError('Original runtime must be healthy before interruption')
    pidfd = os.pidfd_open(int(original['MainPID']))
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = (ctypes.c_int,)
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    libc.inotify_add_watch.restype = ctypes.c_int
    events = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    if events < 0:
        os.close(pidfd)
        raise OSError(ctypes.get_errno(), 'Original inotify observer unavailable')
    try:
        if libc.inotify_add_watch(events, os.fsencode(d.ROOT / 'leases'), CREATE) < 0:
            raise OSError(ctypes.get_errno(), 'Cannot observe original lease namespace')
        request = d.request()['request']
        print(json.dumps({'phase': 'requested', 'request': request}), flush=True)
        directory = d.ROOT / 'leases' / request
        deadline, lease_watch, killed = time.monotonic() + 600, None, False
        while time.monotonic() < deadline:
            if not select.select([events], [], [], 1)[0]:
                if (d.ROOT / 'results' / (request + '.json')).exists():
                    raise ValueError('Proof completed before observed interruption; no kill performed')
                continue
            raw = os.read(events, 65536)
            offset = 0
            while offset < len(raw):
                watch, mask, _cookie, size = struct.unpack_from('iIII', raw, offset)
                name = raw[offset + 16:offset + 16 + size].split(b'\0', 1)[0].decode('ascii')
                offset += 16 + size
                if name == request and lease_watch is None and directory.is_dir():
                    lease_watch = libc.inotify_add_watch(events, os.fsencode(directory), CLOSE_WRITE)
                    if lease_watch < 0: raise OSError(ctypes.get_errno(), 'Cannot observe original grant')
                if watch != lease_watch or name != 'granted.json' or not mask & CLOSE_WRITE:
                    continue
                # The real guard closed this phase only AFTER file + parent fsync.
                intent = g.protected_read(directory / 'intent.json')
                if g.protected_read(directory / 'granted.json') != d.H.encode({
                        'schema': 1, 'intentSha256': g.sha(intent), 'phase': 'granted'}):
                    raise ValueError('Original durable grant required')
                current = g.unit(d.GUARD)
                if current != original:
                    raise ValueError('Original guard process changed before the test kill')
                g.require_quiet(g.unit(g.WATCHER))
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                killed = True
                print(json.dumps({'phase': 'guard-killed-after-real-grant', 'request': request,
                                  'originalWatcherQuiet': True, 'oldGuardPid': original['MainPID']}), flush=True)
                break
            if killed: break
        if not killed:
            raise TimeoutError('No original durable grant observed; no process killed')
        while time.monotonic() < deadline:
            result_path = d.ROOT / 'results' / (request + '.json')
            if result_path.exists():
                result = json.loads(g.protected_read(result_path))
                if result != d.proof_result(context, request, 'no-code-handoff-recovered'):
                    raise ValueError('Independent restart must recover this ORIGINAL proof')
                guard, owner, final_runtime = g.unit(d.GUARD), g.unit(g.WATCHER), g.unit('tesla-waydroid-runtime.service')
                if (guard['ActiveState'] != 'active' or guard['MainPID'] == original['MainPID']
                        or owner['ActiveState'] != 'active' or int(owner['MainPID']) < 2
                        or final_runtime != runtime):
                    raise ValueError('Guard restart and unchanged runtime/original owner required')
                print(json.dumps({'kind': 'live-no-code-guard-interruption-verified', 'request': request,
                    'oldGuardPid': original['MainPID'], 'newGuardPid': guard['MainPID'],
                    'originalWatcherPid': owner['MainPID'], 'unchangedRuntimePid': runtime['MainPID'],
                    'originalGrantObserved': True, 'guardKilledWhileOriginalWatcherQuiet': True,
                    'originalCodeAndBaseReverified': True, 'liveCodeApplied': False,
                    'updatesAllowed': False, 'installable': False}), flush=True)
                return
            time.sleep(1)
        raise TimeoutError('Independent original proof recovery incomplete; preserve journal')
    finally:
        os.close(events)
        os.close(pidfd)


if __name__ == '__main__':
    main()
