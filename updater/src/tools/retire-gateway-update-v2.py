# SPDX-License-Identifier: GPL-3.0-only
"""Retire only the closed development v2 boot barrier, preserving its inodes.

This is not a general service remover or a public installation step. The original
runtime/GPU watcher remain armed; original sources, units and journals survive.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

SOURCE = Path('/var/lib/droiddeck/gateway-update-controller-v2/sources/runtime/portable/gateway-update.py')
SOURCE_PIN = '7bebee809e84baa3de9992878918adfdde38099f60ca9cf193d50723c161a976'
ROOT = Path('/var/lib/droiddeck/gateway-update-v2-retirement')


def retire(d):
    if d.P.sha(d.G.protected_read(SOURCE)) != SOURCE_PIN:
        raise ValueError('Exact original development v2 source required')
    context = d.inspect_controller(); d.inspect_bootstrap(context)
    fd = d.H.protected(d.ROOT, directory=True)
    try: names = sorted(os.listdir(fd))
    finally: os.close(fd)
    if len(names) != 1: raise ValueError('Only the single original closed development attempt may be retired')
    tx = d.Transaction(d.P.digest(names[0])); side, phases = tx.observe()
    if side != 'before' or 'rolled-back.json' not in phases:
        raise ValueError('Complete original-code rollback before retirement required')
    runtime, watcher = tx.g.unit(d.RUNTIME), tx.g.unit(tx.g.WATCHER)
    if (runtime['ActiveState'] != 'active' or int(runtime['MainPID']) < 2
            or watcher['ActiveState'] != 'active' or int(watcher['MainPID']) < 2):
        raise ValueError('Original healthy runtime/GPU watcher required')
    tx.g.require_quiet(tx.g.unit(tx.g.WORKER))
    state = tx.g.unit(tx.unit)
    if state['MainPID'] != '0' or state['ControlPID'] != '0':
        raise ValueError('Closed updater must have no process')
    native = tx.native()
    links = d.boot_links(context)
    before = {str(path): d.E.generation(path.lstat()) for path in links}
    d.T.mkdir_new(ROOT); retained = ROOT / 'links'; d.T.mkdir_new(retained)
    d.T.write_new(ROOT / 'intent.json', d.P.encode({'schema': 1, 'controllerSha256': d.P.sha(d.P.encode(context)),
        'closedDigest': tx.digest, 'rollbackGeneration': d.E.generation((tx.directory / 'rolled-back.json').lstat()),
        'links': before, 'runtime': runtime, 'watcher': watcher}))
    for index, path in enumerate(links):
        d.inspect_controller()
        if d.E.generation(path.lstat()) != before[str(path)]: raise ValueError('Original dependency changed')
        left, right = d.H.protected(path.parent, directory=True), d.H.protected(retained, directory=True)
        try:
            rename = d.ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = (d.ctypes.c_int, d.ctypes.c_char_p, d.ctypes.c_int, d.ctypes.c_char_p, d.ctypes.c_uint)
            rename.restype = d.ctypes.c_int
            if rename(left, path.name.encode('ascii'), right, (str(index) + '.link').encode('ascii'), 1) != 0:
                raise OSError(d.ctypes.get_errno(), 'Original dependency retirement failed')
            os.fsync(left); os.fsync(right)
        finally: os.close(left); os.close(right)
    d.G.command(['/usr/bin/systemctl', 'daemon-reload'])
    for unit in (d.RUNTIME, tx.g.WATCHER):
        raw = d.G.command(['/usr/bin/systemctl', 'show', unit, '-p', 'Requires', '--value']).stdout
        if d.BOOT in raw.decode('ascii').split():
            raise ValueError('Dependency must be detached before stopping the development barrier')
    if tx.g.unit(d.RUNTIME) != runtime or tx.g.unit(tx.g.WATCHER) != watcher:
        raise ValueError('Original runtime and watcher changed during dependency retirement')
    d.G.command(['/usr/bin/systemctl', 'stop', d.BOOT, tx.unit], timeout=30)
    tx.g.require_quiet(tx.g.unit(d.BOOT)); tx.g.require_quiet(tx.g.unit(tx.unit))
    if (tx.g.unit(d.RUNTIME) != runtime or tx.g.unit(tx.g.WATCHER) != watcher or tx.native() != native):
        raise ValueError('Original runtime, watcher and Android generations must stay unchanged')
    d.inspect_controller(); tx.observe()
    identities = {}
    for index, path in enumerate(links):
        target = retained / (str(index) + '.link'); generation = d.E.generation(target.lstat())
        if generation[:6] != before[str(path)][:6] or os.readlink(target) != str(d.SYSTEMD / d.BOOT):
            raise ValueError('Original retired link inode must be retained')
        identities[str(target)] = generation
    result = {'schema': 1, 'kind': 'closed-development-gateway-v2-retired',
        'closedDigest': tx.digest, 'retainedLinks': identities, 'originalRuntimeUnchanged': True,
        'originalGpuWatcherUnchanged': True, 'androidGenerationUnchanged': True,
        'originalControllerSourcesAndUnitsPreserved': True}
    d.T.write_new(ROOT / 'complete.json', d.P.encode(result))
    return result


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or len(sys.argv) != 1:
        raise ValueError('Fixed root Linux development retirement only')
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('original_gateway_v2', SOURCE)
    d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
    print(json.dumps(retire(d), sort_keys=True), flush=True)


if __name__ == '__main__': main()
