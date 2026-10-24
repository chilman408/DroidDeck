# SPDX-License-Identifier: GPL-3.0-only
"""ONE fixed fresh-original private two-role proof on .215; never installs."""
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

spec = importlib.util.spec_from_file_location('actual_admitted_roles', Path(__file__).with_name('runtime-admitted-role-publication.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'


def main():
    T.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed fresh-original private role proof exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    installed = {name: T.A._identity(os.stat(name, follow_symlinks=False)) for name in T.K.ROOTS}
    counts, actual, captured = {'originalHolds': 0, 'layoutReads': 0, 'sourcePairsRead': 0}, T.K.hold_final_install_layout, {}
    started = time.monotonic()

    def admit(*args):
        counts['originalHolds'] += 1
        held = actual(*args)
        plan, read = held['read_plan'], held['read_file']
        def layout():
            raw = plan()
            counts['layoutReads'] += 1
            if T.E._sha(raw) != LAYOUT:
                raise ValueError('Exact original layout generation required')
            captured['roles'] = T._roles(raw)
            return raw
        def pair(name):
            data = read(name)
            counts['sourcePairsRead'] += 1
            if counts['sourcePairsRead'] % 64 == 0:
                print(json.dumps({'phase': 'fresh-original-role-read', 'verified': counts['sourcePairsRead']}), flush=True)
            return data
        return {**held, 'read_plan': layout, 'read_file': pair}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = T.A._read(fd, 16384)
        if T.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original machine metadata generation required')
        parent = tempfile.mkdtemp(prefix='droiddeck-owned-publication-ew-', dir='/root')
        journal = tempfile.mkdtemp(prefix='droiddeck-publication-journal-ew-', dir='/root')
        print(json.dumps({'phase': 'new-private-admitted-role-parents', 'parent': parent, 'journal': journal}), flush=True)
        with mock.patch.object(T.K, 'hold_final_install_layout', side_effect=admit):
            held = T.prepare_admitted_role_publication(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', parent, journal, raw)
        print(json.dumps({'phase': 'admitted-roles-prepared', 'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        for index, role in enumerate(T.P.ROLES, 1):
            report = held['publish_next']()
            if report['privateRolesPublished'] != index:
                raise ValueError('One actual private role move required')
            print(json.dumps({'phase': 'private-role-published', 'role': role, 'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        regular, aliases = 0, 0
        for role, tree in captured['roles'].items():
            held['observe']()
            if held['read'](role, T.MANIFEST) != tree['manifest']:
                raise ValueError('Original role manifest required')
            for name, (size, pin, mode) in tree['files'].items():
                target = Path(parent) / role / name
                data, info = target.read_bytes(), target.lstat()
                if ((len(data), T.E._sha(data)) != (size, pin)
                        or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, mode)):
                    raise ValueError('Independent private role pair/mode differs')
                regular += 1
            for name, target in tree['links'].items():
                if os.readlink(Path(parent) / role / name) != target:
                    raise ValueError('Original direct relative role alias required')
                aliases += 1
            for name, mode in tree['directories'].items():
                if stat.S_IMODE((Path(parent) / role / name).stat().st_mode) != mode:
                    raise ValueError('Original private role directory mode required')
            held['observe']()
        policy = held['observe']()
        if counts != {'originalHolds': 1, 'layoutReads': 1, 'sourcePairsRead': 259} or (regular, aliases) != (261, 4):
            raise ValueError('One fresh original or complete role inventory missing')
        if policy['lastPhase'] != 'config-published' or any(policy[key] for key in T.P.J.FALSE_FLAGS):
            raise ValueError('Private proof must not acquire installation/recovery authority')
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if T.A._identity(info) != T.A._identity(before):
                raise ValueError('Original machine metadata changed')
        if T.A._read(fd, 16384) != raw or installed != {name: T.A._identity(os.stat(name, follow_symlinks=False)) for name in T.K.ROOTS}:
            raise ValueError('Installed original inputs/targets changed')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Descriptor lifecycle or original limit changed')
    print(json.dumps({'kind': 'private-admitted-role-publication-proof', **counts, 'privateRegularFilesVerified': regular,
        'privateAliasesVerified': aliases, 'parent': parent, 'journal': journal, 'descriptorsClosed': True,
        'descriptorLimitUnchanged': True, 'installedTargetIdentitiesUnchanged': True,
        'elapsedSeconds': round(time.monotonic() - started, 3), 'publication': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, T.R.M.subprocess.SubprocessError, T.E.P.configparser.Error):
        print('Private admitted-role proof incomplete; preserve both parents; no installation performed', file=sys.stderr)
        sys.exit(1)
