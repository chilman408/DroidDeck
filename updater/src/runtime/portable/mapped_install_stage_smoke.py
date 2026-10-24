# SPDX-License-Identifier: GPL-3.0-only
"""ONE new private mapped copy from original inputs on .215; never installs."""
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

spec = importlib.util.spec_from_file_location('actual_mapped_stage', Path(__file__).with_name('runtime-mapped-install-stage.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'


def main():
    M.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed original-input private mapped staging proof is exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    started = time.monotonic()
    installed = {name: M.A._identity(os.stat(name, follow_symlinks=False)) for name in M.K.ROOTS}
    original = M.K.hold_final_install_layout
    counts = {'originalHolds': 0, 'sourcePairsRead': 0}

    def admit(*args):
        counts['originalHolds'] += 1
        held = original(*args)
        if M.E._sha(held['read_plan']()) != LAYOUT:
            raise ValueError('Exact original final-layout generation required')
        read_file = held['read_file']
        def read(name):
            data = read_file(name)
            counts['sourcePairsRead'] += 1
            if counts['sourcePairsRead'] % 64 == 0:
                print(json.dumps({'phase': 'original-mapped-source-read', 'verified': counts['sourcePairsRead']}), flush=True)
            return data
        return {**held, 'read_file': read}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = M.A._read(fd, 16384)
        if M.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original metadata generation required')
        parent = tempfile.mkdtemp(prefix='droiddeck-mapped-install-proof-20261017ep-', dir='/root')
        print(json.dumps({'phase': 'new-private-mapped-parent', 'path': parent}), flush=True)
        with mock.patch.object(M.K, 'hold_final_install_layout', side_effect=admit):
            held = M.publish_mapped_install_candidate(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', parent, raw)
        print(json.dumps({'phase': 'mapped-candidate-published', 'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        layout = held['read']('final-layout.json')
        records, _ = M._layout(layout)
        manifest = M._manifest(layout, records)
        expected = {**records, M.A.MANIFEST: (len(manifest), M.E._sha(manifest))}
        if set(held['files']) != set(expected):
            raise ValueError('Exact private261 candidate inventory required')
        verified = 0
        for name, pair in sorted(expected.items()):
            data = held['read'](name)
            if (len(data), M.E._sha(data)) != pair:
                raise ValueError('Independent mapped candidate pair differs')
            verified += 1
            if verified % 64 == 0:
                print(json.dumps({'phase': 'private-mapped-read-verification', 'verified': verified}), flush=True)
        held['check']()
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if M.A._identity(info) != M.A._identity(before):
                raise ValueError('Original machine metadata changed')
        if M.A._read(fd, 16384) != raw or counts != {'originalHolds': 1, 'sourcePairsRead': 259} or verified != 261:
            raise ValueError('Single original context or complete mapped inventory required')
        if installed != {name: M.A._identity(os.stat(name, follow_symlinks=False)) for name in M.K.ROOTS}:
            raise ValueError('Installed target identities changed')
        policy = held['policy']
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'private-mapped-install-stage-proof', **counts, 'candidatePairsVerified': verified,
        'candidateParent': parent, 'installedTargetIdentitiesUnchanged': True, 'descriptorsClosed': True,
        'descriptorLimitUnchanged': True, 'staging': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, M.subprocess.SubprocessError, M.E.P.configparser.Error):
        print('Private mapped staging proof incomplete; preserve new parent before retry; no installation performed', file=sys.stderr)
        sys.exit(1)
