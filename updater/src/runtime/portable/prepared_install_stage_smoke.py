# SPDX-License-Identifier: GPL-3.0-only
"""ONE fixed original-input private prepared proof on .215; never installs."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
import tempfile
import time
from unittest import mock

spec = importlib.util.spec_from_file_location('actual_prepared_stage', Path(__file__).with_name('runtime-prepared-stage.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'


def main():
    R.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed original-input new private prepared proof exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    installed = {name: R.A._identity(os.stat(name, follow_symlinks=False)) for name in R.K.ROOTS}
    started, original = time.monotonic(), R.K.hold_final_install_layout
    counts = {'originalHolds': 0, 'layoutReads': 0, 'sourcePairsRead': 0}

    def admit(*args):
        counts['originalHolds'] += 1
        held = original(*args)
        read_plan, read_file = held['read_plan'], held['read_file']
        def plan():
            data = read_plan()
            counts['layoutReads'] += 1
            if R.E._sha(data) != LAYOUT:
                raise ValueError('Exact original final-layout generation required')
            return data
        def read(name):
            data = read_file(name)
            counts['sourcePairsRead'] += 1
            if counts['sourcePairsRead'] % 64 == 0:
                print(json.dumps({'phase': 'original-prepared-source-read', 'verified': counts['sourcePairsRead']}), flush=True)
            return data
        return {**held, 'read_file': read, 'read_plan': plan}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = R.A._read(fd, 16384)
        if R.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original metadata generation required')
        parent = tempfile.mkdtemp(prefix='droiddeck-prepared-install-proof-20261017er-', dir='/root')
        print(json.dumps({'phase': 'new-private-prepared-parent', 'path': parent}), flush=True)
        with mock.patch.object(R.K, 'hold_final_install_layout', side_effect=admit):
            held = R.publish_prepared_install_tree(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', parent, raw)
        print(json.dumps({'phase': 'prepared-tree-published', 'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        tree = R.Q._spec(held['read']('final-layout.json'))
        if set(held['files']) != set(tree['files']) or set(held['links']) != set(tree['links']):
            raise ValueError('Exact private prepared261 regular/four-alias inventory required')
        verified = 0
        for name, (size, pin, mode) in sorted(tree['files'].items()):
            data = held['read'](name)
            info = os.lstat(str(Path(parent) / 'prepared' / name))
            if ((len(data), R.E._sha(data)) != (size, pin)
                    or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, mode)):
                raise ValueError('Independent prepared pair or mode differs')
            verified += 1
            if verified % 64 == 0:
                print(json.dumps({'phase': 'private-prepared-read-verification', 'verified': verified}), flush=True)
        for name, target in tree['links'].items():
            if held['read_link'](name) != target:
                raise ValueError('Prepared direct library alias differs')
        held['check']()
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if R.A._identity(info) != R.A._identity(before):
                raise ValueError('Original machine metadata changed')
        if R.A._read(fd, 16384) != raw or counts != {'originalHolds': 1, 'layoutReads': 1, 'sourcePairsRead': 259} or verified != 261:
            raise ValueError('Single original context or prepared inventory incomplete')
        if installed != {name: R.A._identity(os.stat(name, follow_symlinks=False)) for name in R.K.ROOTS}:
            raise ValueError('Installed target identities changed')
        policy = held['policy']
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Prepared proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'private-prepared-install-stage-proof', **counts, 'preparedPairsVerified': verified,
        'preparedAliasesVerified': 4, 'preparedParent': parent, 'installedTargetIdentitiesUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True, 'staging': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, R.M.subprocess.SubprocessError, R.E.P.configparser.Error):
        print('Private prepared proof incomplete; preserve parent before retry; no installation performed', file=sys.stderr)
        sys.exit(1)
