# SPDX-License-Identifier: GPL-3.0-only
"""One retained, inactive .215 candidate proof. Never deploys or activates it."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
import tempfile

spec = importlib.util.spec_from_file_location('owned_candidate_proof', Path(__file__).with_name('runtime-owned-install-stage.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


def main():
    F.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed one-shot candidate proof is exposed')
    initial_descriptors = set(os.listdir('/proc/self/fd'))
    initial_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    with ExitStack() as stack:
        fd = os.open('/etc/droiddeck/runtime.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original proof metadata required')
        raw = F.A._read(fd, 16384)
        if F.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original metadata hash required')
        parent = tempfile.mkdtemp(prefix='droiddeck-owned-install-stage-proof-20261017ef-', dir='/root')
        print(json.dumps({'phase': 'admit-and-publish-private-candidate', 'proofParent': parent}), flush=True)
        held = F.publish_owned_install_candidate(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
            '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', parent, raw)
        print(json.dumps({'phase': 'verify-created-files', 'files': len(held['expectedRecords'])}), flush=True)
        count = 0
        for name, expected in held['expectedRecords'].items():
            copied = held['read'](name)
            if (len(copied), F.E._sha(copied)) != expected:
                raise ValueError('Independent created payload pair differs')
            count += 1
            if count % 64 == 0:
                print(json.dumps({'phase': 'verify-created-files', 'verified': count}), flush=True)
        candidate = Path(parent) / 'candidate'
        all_files = [p for p in candidate.rglob('*') if p.is_file()]
        if count != 252 or len(all_files) != 252 or any(p.is_symlink() for p in candidate.rglob('*')):
            raise ValueError('Exact private regular candidate inventory required')
        for path in (candidate, *candidate.rglob('*')):
            info = path.lstat()
            wanted = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, wanted):
                raise ValueError('New private candidate ownership and modes required')
        plan = held['read']('plan.json')
        if F.E._sha(plan) != '4a6eae499d901e5d6c3e9b63a47aa28744e593c834fcd1b6fcae54c6a7d2b121':
            raise ValueError('Original admitted owned plan required')
        for info in (os.fstat(fd), os.stat('/etc/droiddeck/runtime.json', follow_symlinks=False)):
            if F.A._identity(info) != F.A._identity(before):
                raise ValueError('Proof metadata generation changed')
        if F.A._read(fd, 16384) != raw:
            raise ValueError('Original proof metadata bytes changed')
        held['check']()
        policy = held['policy']
    if set(os.listdir('/proc/self/fd')) != initial_descriptors or resource.getrlimit(resource.RLIMIT_NOFILE) != initial_limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'retained-inert-owned-install-candidate-proof', 'bytePairsVerified': count,
        'descriptorLimitUnchanged': True, 'descriptorsClosed': True, 'candidate': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, F.E.P.configparser.Error):
        print('Owned staging proof incomplete; preserve printed proof parent and inspect before retry', file=sys.stderr)
        sys.exit(1)
