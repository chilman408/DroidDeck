# SPDX-License-Identifier: GPL-3.0-only
"""Detach only the closed v3 development controller before public enrollment.

Retains all original source/unit/history/transaction inodes. Not a public upgrade
step, a general unit remover, or permission to adopt an incomplete transaction.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys

SOURCE = Path('/var/lib/droiddeck/gateway-update-controller-v3/sources/runtime/portable/gateway-update-chain.py')
SOURCE_PIN = 'c0f444355b05b2fac7d9a97db474245e75fe1dd2f407f5e7e36ef2123198ed5a'
ROOT = Path('/var/lib/droiddeck/gateway-update-v3-retirement')


def retire(c):
    d = c.B
    if c.P.sha(c.G.protected_read(SOURCE)) != SOURCE_PIN: raise ValueError('Exact original development chain source required')
    context = c.inspect_controller(); d.inspect_bootstrap(context)
    rows = c.records()
    if not 1 <= len(rows) <= 2: raise ValueError('Only original bounded closed development history may be retired')
    tx = c.current_view(rows); side, phases = tx.observe()
    if side != 'before' or 'rolled-back.json' not in phases: raise ValueError('Original complete current rollback required')
    runtime, watcher = tx.g.unit(d.RUNTIME), tx.g.unit(tx.g.WATCHER)
    if (runtime['ActiveState'] != 'active' or int(runtime['MainPID']) < 2
            or watcher['ActiveState'] != 'active' or int(watcher['MainPID']) < 2):
        raise ValueError('Healthy original runtime and GPU watcher required')
    tx.g.require_quiet(tx.g.unit(tx.g.WORKER))
    state = tx.g.unit(tx.unit)
    if any(state[k] != '0' for k in ('MainPID', 'ControlPID')): raise ValueError('Closed updater must have no process')
    native = tx.native(); links = d.boot_links(context)
    before = {str(path): c.E.generation(path.lstat()) for path in links}
    c.T.mkdir_new(ROOT); retained = ROOT / 'links'; c.T.mkdir_new(retained)
    c.T.write_new(ROOT / 'intent.json', c.P.encode({'schema': 1, 'contextSha256': c.P.sha(c.P.encode(context)),
        'closedDigest': tx.digest, 'history': rows, 'links': before, 'runtime': runtime, 'watcher': watcher}))
    for index, path in enumerate(links):
        c.inspect_controller(); c.current_view(c.records())
        if c.E.generation(path.lstat()) != before[str(path)]: raise ValueError('Original bootstrap link required')
        left, right = c.H.protected(path.parent, directory=True), c.H.protected(retained, directory=True)
        try:
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            rename.restype = ctypes.c_int
            if rename(left, path.name.encode('ascii'), right, (str(index) + '.link').encode('ascii'), 1) != 0:
                raise OSError(ctypes.get_errno(), 'Original bootstrap link retirement failed')
            os.fsync(left); os.fsync(right)
        finally: os.close(left); os.close(right)
    c.G.command(['/usr/bin/systemctl', 'daemon-reload'])
    for unit in (d.RUNTIME, tx.g.WATCHER):
        requires = c.G.command(['/usr/bin/systemctl', 'show', unit, '-p', 'Requires', '--value']).stdout.decode('ascii').split()
        if d.BOOT in requires: raise ValueError('Original barrier still attached; cannot stop it')
    if tx.g.unit(d.RUNTIME) != runtime or tx.g.unit(tx.g.WATCHER) != watcher:
        raise ValueError('Original runtime and watcher changed during retirement')
    c.G.command(['/usr/bin/systemctl', 'stop', d.BOOT, tx.unit], timeout=30)
    tx.g.require_quiet(tx.g.unit(d.BOOT)); tx.g.require_quiet(tx.g.unit(tx.unit))
    if tx.g.unit(d.RUNTIME) != runtime or tx.g.unit(tx.g.WATCHER) != watcher or tx.native() != native:
        raise ValueError('Runtime, watcher and Android generations must remain unchanged')
    c.current_view(c.records()); c.inspect_controller()
    identities = {}
    for index, path in enumerate(links):
        target = retained / (str(index) + '.link'); generation = c.E.generation(target.lstat())
        if generation[:6] != before[str(path)][:6] or os.readlink(target) != str(d.SYSTEMD / d.BOOT):
            raise ValueError('Same original bootstrap link inode must be retained')
        identities[str(target)] = generation
    result = {'schema': 1, 'kind': 'closed-development-chain-v3-retired', 'closedDigest': tx.digest,
        'attemptsPreserved': len(rows), 'retainedLinks': identities, 'originalRuntimeUnchanged': True,
        'originalGpuWatcherUnchanged': True, 'androidGenerationUnchanged': True}
    c.T.write_new(ROOT / 'complete.json', c.P.encode(result))
    return result


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or len(sys.argv) != 1: raise ValueError('Fixed root Linux development retirement only')
    os.umask(0o077)
    s = importlib.util.spec_from_file_location('accepted_chain_v3', SOURCE); c = importlib.util.module_from_spec(s); s.loader.exec_module(c)
    print(json.dumps(retire(c), sort_keys=True), flush=True)


if __name__ == '__main__': main()
