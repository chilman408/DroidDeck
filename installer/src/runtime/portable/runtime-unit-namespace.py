# SPDX-License-Identifier: GPL-3.0-only
"""Read-only owned systemd namespace admission; never reload or install units."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

spec = importlib.util.spec_from_file_location('unit_registration_paths', Path(__file__).with_name('runtime-registration-paths.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
A, S = R.A, R.S
LOAD_PATHS = tuple(PurePosixPath(p) for p in (
    '/etc/systemd/system.control', '/run/systemd/system.control', '/run/systemd/transient',
    '/run/systemd/generator.early', '/etc/systemd/system', '/etc/systemd/system.attached',
    '/run/systemd/system', '/run/systemd/system.attached', '/run/systemd/generator',
    '/usr/local/lib/systemd/system', '/usr/lib/systemd/system', '/run/systemd/generator.late'))
OWNED = tuple(sorted(path.name for path in R.TARGETS if path.parent == R.SYSTEMD))
SUFFIXES = ('.wants', '.requires', '.upholds')
MAX_ENTRIES, MAX_DIRECTORIES = 8192, 256


def _query(*arguments):
    result = subprocess.run(['/usr/bin/systemctl', *arguments], cwd='/',
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'},
        capture_output=True, check=True, timeout=5)
    if not 0 < len(result.stdout) <= 16384:
        raise ValueError('Bounded system manager metadata required')
    return result.stdout.decode('ascii')


def _manager():
    if tuple(_query('show', '--property=UnitPath', '--value').split()) != tuple(map(str, LOAD_PATHS)):
        raise ValueError('Exact reviewed system manager unit paths required')
    raw = _query('show', '--property=Id', '--property=LoadState', '--property=ActiveState',
                 '--property=FragmentPath', '--property=DropInPaths', *OWNED)
    blocks = raw.strip().split('\n\n')
    seen = set()
    for block in blocks:
        rows = [line.split('=', 1) for line in block.splitlines()]
        if any(len(row) != 2 for row in rows) or len(rows) != 5:
            raise ValueError('Closed system manager unit metadata required')
        value = dict(rows)
        if (set(value) != {'Id', 'LoadState', 'ActiveState', 'FragmentPath', 'DropInPaths'}
                or value['Id'] not in OWNED or value['Id'] in seen):
            raise ValueError('Exact owned system manager units required')
        if (value['LoadState'], value['ActiveState'], value['FragmentPath'], value['DropInPaths']) != ('not-found', 'inactive', '', ''):
            raise ValueError('Existing owned system manager unit must never be adopted')
        seen.add(value['Id'])
    if seen != set(OWNED):
        raise ValueError('Complete owned system manager metadata required')


def dropin_names():
    names = {'service.d'}
    for unit in R.UNITS:
        stem = unit.removesuffix('.service')
        names.add(unit + '.d')
        names.update(stem[:index + 1] + '.service.d' for index, char in enumerate(stem) if char == '-')
    return tuple(sorted(names))


def hold_unit_namespace(stack):
    A._root_linux()
    _manager()
    nodes, missing = {}, {}

    def directory(path):
        if path in nodes:
            return nodes[path][0]
        if path in missing:
            return None
        parent = None if path == PurePosixPath('/') else directory(path.parent)
        if path != PurePosixPath('/') and parent is None:
            missing[path] = None
            return None
        if len(nodes) >= MAX_DIRECTORIES:
            raise ValueError('Bounded systemd directory descriptors required')
        name = '/' if parent is None else path.name
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            missing[path] = (parent, name)
            return None
        stack.callback(os.close, fd)
        identity = S._directory(os.fstat(fd), False)
        nodes[path] = (fd, parent, name, identity)
        return fd

    def scan(fd, budget):
        before = A._identity(os.fstat(fd))
        rows = {}
        with os.scandir(fd) as entries:
            for entry in entries:
                budget[0] += 1
                if budget[0] > MAX_ENTRIES:
                    raise ValueError('Bounded systemd namespace inventory required')
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                target = os.readlink(entry.name, dir_fd=fd) if stat.S_ISLNK(info.st_mode) else None
                if target is not None and len(target) > 4096:
                    raise ValueError('Bounded unit alias target required')
                if A._identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)) != A._identity(info):
                    raise ValueError('Stable unit namespace entry required')
                rows[entry.name] = (A._identity(info), target)
        if A._identity(os.fstat(fd)) != before:
            raise ValueError('Stable unit namespace directory required')
        return rows

    budget = [0]
    roots = [(path, directory(path)) for path in LOAD_PATHS]
    root_rows = {path: scan(fd, budget) for path, fd in roots if fd is not None}
    aliases = set(OWNED)
    edges = [(name, PurePosixPath(target).name) for rows in root_rows.values()
             for name, (_, target) in rows.items() if target is not None]
    while True:
        expanded = aliases | {name for name, target in edges if target in aliases}
        if expanded == aliases:
            break
        aliases = expanded
    for rows in root_rows.values():
        if set(rows).intersection(aliases):
            raise ValueError('Existing owned unit, mask or alias must never be adopted')

    children = []
    for path, fd in roots:
        if fd is None:
            continue
        rows = root_rows[path]
        for name in dropin_names():
            child = directory(path / name)
            if child is not None:
                inventory = scan(child, budget)
                if inventory:
                    raise ValueError('Foreign exact, prefix or global service drop-in refused')
                children.append((child, inventory))
        for name in rows:
            if not name.endswith(SUFFIXES):
                continue
            child = directory(path / name)
            if child is None:
                raise ValueError('Dependency directory changed during admission')
            inventory = scan(child, budget)
            if (name in {unit + suffix for unit in OWNED for suffix in SUFFIXES} and inventory
                    or set(inventory).intersection(aliases)
                    or any(target is not None and PurePosixPath(target).name in aliases for _, target in inventory.values())):
                raise ValueError('Foreign owned unit enablement or dependency refused')
            children.append((child, inventory))

    def check():
        A._root_linux()
        _manager()
        for fd, parent, name, identity in nodes.values():
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if S._directory(info, False) != identity:
                    raise ValueError('Original protected systemd ancestor required')
        for absent in missing.values():
            if absent is not None:
                R._absent(*absent)
        fresh = [0]
        for path, fd in roots:
            if fd is not None and scan(fd, fresh) != root_rows[path]:
                raise ValueError('Original systemd unit inventory required')
        for fd, inventory in children:
            if scan(fd, fresh) != inventory:
                raise ValueError('Original systemd drop-in and dependency inventory required')
        _manager()

    check()
    return {'check': check, 'policy': {
        'kind': 'held-owned-systemd-namespace', 'ownedSystemdNamespacePreflightVerified': True,
        'managerUnitPathVerified': True, 'managerOwnedUnitsAbsentVerified': True,
        'unitSearchPathsChecked': len(LOAD_PATHS), 'dropInNamesCheckedPerPath': len(dropin_names()),
        'ownedUnitNamesChecked': len(OWNED), 'namespaceEntriesObserved': budget[0],
        **dict.fromkeys(('effectiveSystemdNamespaceVerified', 'registrationNamespaceVerified',
            'targetReserved', 'targetOwnershipClaimed', 'filesWritten', 'servicesRegistered',
            'servicesActivated', 'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation'), False)}}


def hold_final_unit_namespace(stack, user, source, recipes, raw):
    A._root_linux()
    R.K.E.P.C._bounded(raw)
    namespace = hold_unit_namespace(stack)
    held = R.hold_final_registration_paths(stack, user, source, recipes, raw)

    def check():
        namespace['check']()
        held['check']()
        namespace['check']()

    def read(method, *arguments):
        check()
        data = held[method](*arguments)
        check()
        return data

    check()
    return {'check': check, 'read_plan': lambda: read('read_plan'),
        'read_file': lambda name: read('read_file', name),
        'policy': {**held['policy'], **namespace['policy'], 'kind': 'held-final-owned-systemd-namespace'}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as stack:
        if argv == ['--inspect-unit-namespace']:
            held = hold_unit_namespace(stack)
        elif len(argv) == 5 and argv[0] == '--inspect-final-unit-namespace' and argv[4] == '-':
            held = hold_final_unit_namespace(stack, argv[1], argv[2], argv[3], sys.stdin.buffer.read(R.K.E.P.C.LIMIT + 1))
        else:
            raise ValueError('Only fixed read-only systemd namespace preflight is exposed')
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError,
            subprocess.SubprocessError, R.K.E.P.configparser.Error):
        print('Systemd namespace preflight refused; no files or services changed', file=sys.stderr)
        sys.exit(1)
