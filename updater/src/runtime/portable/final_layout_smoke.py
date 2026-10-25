# SPDX-License-Identifier: GPL-3.0-only
"""Fixed original-input read-only placement proof on .215, never installs."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys

spec = importlib.util.spec_from_file_location('actual_final_layout', Path(__file__).with_name('runtime-final-layout.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)


def main():
    K.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed original-input read-only placement proof is exposed')
    initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original bounded machine metadata required')
        raw = K.A._read(fd, 16384)
        if K.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original metadata generation required')
        held = K.hold_final_install_layout(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
            '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', raw)
        value = json.loads(held['read_plan']())
        if value['ownedInstallPlanSha256'] != '4a6eae499d901e5d6c3e9b63a47aa28744e593c834fcd1b6fcae54c6a7d2b121':
            raise ValueError('Original owned plan required')
        count = 0
        for row in value['files']:
            data = held['read_file'](row['path'])
            if (len(data), K.E._sha(data)) != (row['size'], row['sha256']):
                raise ValueError('Independent destination source pair differs')
            count += 1
            if count % 64 == 0:
                print(json.dumps({'phase': 'final-source-read-verification', 'verified': count}), flush=True)
        if (count, len(value['links']), len(value['registrationTargets'])) != (259, 4, 9):
            raise ValueError('Exact final metadata counts required')
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if K.A._identity(info) != K.A._identity(before):
                raise ValueError('Original metadata namespace changed')
        if K.A._read(fd, 16384) != raw:
            raise ValueError('Original metadata bytes changed')
        held['check']()
        policy = held['policy']
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'read-only-final-layout-source-proof', 'sourcePairsVerified': count,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True, 'placement': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, K.subprocess.SubprocessError, K.E.P.configparser.Error):
        print('Final placement proof incomplete; no files or registrations changed', file=sys.stderr)
        sys.exit(1)
