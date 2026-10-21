# SPDX-License-Identifier: GPL-3.0-only
"""Fixed read-only fresh admission of the retained ef candidate on .215."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys

spec = importlib.util.spec_from_file_location('retained_owned_candidate_proof', Path(__file__).with_name('runtime-owned-install-admission.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def main():
    G.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed read-only retained candidate proof is exposed')
    initial = set(os.listdir('/proc/self/fd'))
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    with ExitStack() as stack:
        fd = os.open('/etc/droiddeck/runtime.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original proof metadata required')
        raw = G.A._read(fd, 16384)
        if G.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original metadata hash required')
        print(json.dumps({'phase': 'fresh-original-input-and-retained-candidate-admission'}), flush=True)
        held = G.hold_owned_install_candidate(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
            '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources',
            '/root/droiddeck-owned-install-stage-proof-20261017ef-_vp8yebe', raw)
        count = 0
        for name, expected in held['expectedRecords'].items():
            data = held['read'](name)
            if (len(data), G.E._sha(data)) != expected:
                raise ValueError('Fresh independent candidate pair differs')
            count += 1
            if count % 64 == 0:
                print(json.dumps({'phase': 'retained-read-verification', 'verified': count}), flush=True)
        if count != 252 or G.E._sha(held['read_plan']()) != '4a6eae499d901e5d6c3e9b63a47aa28744e593c834fcd1b6fcae54c6a7d2b121':
            raise ValueError('Original candidate plan and complete read inventory required')
        for info in (os.fstat(fd), os.stat('/etc/droiddeck/runtime.json', follow_symlinks=False)):
            if G.A._identity(info) != G.A._identity(before):
                raise ValueError('Proof metadata generation changed')
        if G.A._read(fd, 16384) != raw:
            raise ValueError('Original proof metadata bytes changed')
        held['check']()
        policy = held['policy']
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'fresh-retained-candidate-admission-proof', 'bytePairsVerified': count,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True, 'candidate': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, G.E.P.configparser.Error):
        print('Retained candidate proof refused; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
