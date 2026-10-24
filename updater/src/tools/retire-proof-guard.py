# SPDX-License-Identifier: GPL-3.0-only
"""Retire ONLY the closed first no-code prototype; retain sources and unit inodes.

This is explicit disposable-development maintenance, never installer/update code.
Partial retirement is retained for inspection, never automatically replayed.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ENTRY = Path('/var/lib/droiddeck/update-supervision/sources/runtime/portable/update-guard.py')


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or len(args) != 1 or not re.fullmatch('[a-f0-9]{64}', args[0]):
        raise ValueError('Explicit root original prototype context SHA256 required')
    spec = importlib.util.spec_from_file_location('original_no_code_guard', ENTRY)
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    context = d.inspect()
    if d.G.sha(d.G.protected_read(d.ROOT / 'context.json')) != args[0]:
        raise ValueError('Independent original prototype context required')
    names = os.listdir(d.ROOT / 'requests')
    if len(names) > 128 or any(not re.fullmatch(r'[a-f0-9]{32}\.json', name) for name in names):
        raise ValueError('Exact bounded original requests required')
    for name in names:
        ident = name[:-5]
        raw = d.G.protected_read(d.ROOT / 'requests' / name)
        if raw != d.H.encode({'schema': 1, 'purpose': 'handoff-proof-only', 'request': ident}):
            raise ValueError('Foreign request prevents retirement')
        result = json.loads(d.G.protected_read(d.ROOT / 'results' / name))
        if (result.get('kind') not in ('no-code-proof-aborted-before-intent', 'no-code-handoff-proof', 'no-code-handoff-recovered')
                or result != d.proof_result(context, ident, result['kind'])):
            raise ValueError('Unclosed or foreign proof prevents retirement')
    before = d.prove(context['mode'], context['package'])
    if before.get('originalInstallationOwnershipVerified') is not True:
        raise ValueError('Fresh original base required before retirement')
    module, _active = d.activation(context)
    module.G.command(['/usr/bin/systemctl', 'stop', d.GUARD], timeout=30)
    module.G.require_quiet(module.G.unit(d.GUARD))
    if module.G.unit(module.G.WATCHER)['ActiveState'] != 'active':
        raise ValueError('Original Android supervision must remain armed')
    d.inspect()
    destination = d.ROOT / 'retired-units'
    d.T.mkdir_new(destination)
    raw = d.G.protected_read(d.ROOT / 'units.json')
    d.T.write_new(destination / 'intent.json', d.H.encode({'schema': 1, 'kind': 'closed-no-code-guard-retirement',
        'contextSha256': args[0], 'unitsSha256': d.G.sha(raw), 'sourceAndRequestRecordsRetained': True}))
    parent, retained = d.H.protected(d.SYSTEMD, directory=True), d.H.protected(destination, directory=True)
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        units = json.loads(raw)['identities']
        for name in (d.GUARD, d.WORKER):
            original = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if [original.st_dev, original.st_ino] != units[name]:
                raise ValueError('Original prototype unit inode required')
            if rename(parent, name.encode(), retained, name.encode(), 1) != 0:
                raise OSError(ctypes.get_errno(), 'Original unit retention incomplete')
            os.fsync(retained); os.fsync(parent)
        d.H.write_new(retained, 'complete.json', d.H.encode({'schema': 1, 'kind': 'closed-no-code-guard-retirement',
            'contextSha256': args[0], 'unitsSha256': d.G.sha(raw), 'sourceAndRequestRecordsRetained': True}))
    finally:
        os.close(parent); os.close(retained)
    module.G.command(['/usr/bin/systemctl', 'daemon-reload'])
    print(json.dumps({'kind': 'closed-no-code-guard-retired', 'originalWatchStillArmed': True,
                      'originalUnitInodesRetained': True, 'sourceAndProofRecordsRetained': True,
                      'liveCodeApplied': False, 'updatesAllowed': False}, sort_keys=True))


if __name__ == '__main__':
    main(sys.argv[1:])
