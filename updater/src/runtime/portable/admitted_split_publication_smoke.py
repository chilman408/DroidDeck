# SPDX-License-Identifier: GPL-3.0-only
"""ONE fixed fresh-original PRIVATE split-device proof on .215; never installs."""
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

spec = importlib.util.spec_from_file_location('actual_admitted_split', Path(__file__).with_name('runtime-admitted-split-publication.py'))
Z = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Z)
T, X = Z.T, Z.X
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'
SETTINGS = '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3'


def main():
    Z.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed fresh-original private split proof exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    installed = {name: Z.A._identity(os.stat(name, follow_symlinks=False)) for name in Z.K.ROOTS}
    counts = {'originalHolds': 0, 'layoutReads': 0, 'sourcePairsRead': 0}
    actual, captured, started = Z.K.hold_final_install_layout, {}, time.monotonic()

    def admit(*args):
        counts['originalHolds'] += 1
        original = actual(*args)
        plan, read = original['read_plan'], original['read_file']
        def layout():
            raw = plan()
            counts['layoutReads'] += 1
            if Z.E._sha(raw) != LAYOUT:
                raise ValueError('Exact original layout generation required')
            captured['roles'] = T._roles(raw)
            return raw
        def pair(name):
            data = read(name)
            counts['sourcePairsRead'] += 1
            if counts['sourcePairsRead'] % 64 == 0:
                print(json.dumps({'phase': 'fresh-split-original-read', 'verified': counts['sourcePairsRead']}), flush=True)
            return data
        return {**original, 'read_plan': layout, 'read_file': pair}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = Z.A._read(fd, 16384)
        if Z.E._sha(raw) != SETTINGS:
            raise ValueError('Original machine metadata generation required')
        X._different_devices((os.stat('/root').st_dev, os.stat('/run').st_dev))
        runtime = tempfile.mkdtemp(prefix='droiddeck-split-runtime-ez-', dir='/root')
        print(json.dumps({'phase': 'new-private-split-runtime', 'parent': runtime}), flush=True)
        config = tempfile.mkdtemp(prefix='droiddeck-split-config-ez-', dir='/run')
        print(json.dumps({'phase': 'new-ephemeral-private-split-config', 'parent': config}), flush=True)
        journal = tempfile.mkdtemp(prefix='droiddeck-publication-journal-ez-', dir='/root')
        print(json.dumps({'phase': 'new-private-split-journal', 'parent': journal}), flush=True)
        parents = {'runtime': runtime, 'config': config}
        with mock.patch.object(Z.K, 'hold_final_install_layout', side_effect=admit):
            held = Z.prepare_admitted_split_publication(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', runtime, config, journal, raw)
        print(json.dumps({'phase': 'admitted-split-prepared', 'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        for index, role in enumerate(X.ROLES, 1):
            report = held['publish_next']()
            if report['privateRolesPublished'] != index:
                raise ValueError('One actual private split-role move required')
            print(json.dumps({'phase': 'private-split-role-published', 'role': role,
                'elapsedSeconds': round(time.monotonic() - started, 3)}), flush=True)
        regular, aliases = 0, 0
        for role, tree in captured['roles'].items():
            held['observe']()
            if held['read'](role, T.MANIFEST) != tree['manifest']:
                raise ValueError('Original role manifest required')
            for name, (size, pin, mode) in tree['files'].items():
                target = Path(parents[role]) / role / name
                data, info = target.read_bytes(), target.lstat()
                if ((len(data), Z.E._sha(data)) != (size, pin)
                        or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, mode)):
                    raise ValueError('Independent original split payload bytes/modes differ')
                regular += 1
            for name, target in tree['links'].items():
                if os.readlink(Path(parents[role]) / role / name) != target:
                    raise ValueError('Original direct relative library alias required')
                aliases += 1
            for name, mode in tree['directories'].items():
                if stat.S_IMODE((Path(parents[role]) / role / name).stat().st_mode) != mode:
                    raise ValueError('Original reviewed private directory mode required')
            held['observe']()
        policy = held['observe']()
        devices = {role: os.stat(parent).st_dev for role, parent in parents.items()}
        if counts != {'originalHolds': 1, 'layoutReads': 1, 'sourcePairsRead': 259} or (regular, aliases) != (261, 4):
            raise ValueError('One original or complete split inventory missing')
        X._different_devices(tuple(devices[role] for role in X.ROLES))
        if (not policy['privateCrossDeviceRolePublicationVerified'] or policy['lastPhase'] != 'config-published'
                or any(policy[key] for key in (*X.J.FALSE_FLAGS, 'globallyAtomic', 'crossFilesystemPublicationVerified',
                    'persistentConfigDestinationVerified', 'restartRecoveryVerified', 'powerLossRecoveryVerified'))):
            raise ValueError('Private proof must not acquire installation or recovery authority')
        if len(os.listdir('/proc/self/fd')) > policy['splitPayloadDescriptorUpperBound']:
            raise ValueError('Retained split descriptor budget exceeded')
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if Z.A._identity(info) != Z.A._identity(before):
                raise ValueError('Original machine metadata generation changed')
        if Z.A._read(fd, 16384) != raw or installed != {name: Z.A._identity(os.stat(name, follow_symlinks=False)) for name in Z.K.ROOTS}:
            raise ValueError('Original machine metadata or installed target identity changed')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Descriptor lifecycle or original limit changed')
    print(json.dumps({'kind': 'private-admitted-split-publication-proof', **counts,
        'privateRegularFilesVerified': regular, 'privateAliasesVerified': aliases,
        'parents': parents, 'devices': devices, 'journal': journal, 'descriptorsClosed': True,
        'descriptorLimitUnchanged': True, 'installedTargetIdentitiesUnchanged': True,
        'elapsedSeconds': round(time.monotonic() - started, 3), 'publication': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError,
            T.R.M.subprocess.SubprocessError, Z.E.P.configparser.Error):
        print('Private admitted-split proof incomplete; preserve ALL THREE parents; no installation performed', file=sys.stderr)
        sys.exit(1)
