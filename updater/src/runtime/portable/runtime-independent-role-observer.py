# SPDX-License-Identifier: GPL-3.0-only
"""Read-only fresh-original inspection of published private roles, NOT adoption."""
import json
import os
from pathlib import Path
import re
import stat
import sys
import importlib.util

spec = importlib.util.spec_from_file_location('independently_observed_roles', Path(__file__).with_name('runtime-admitted-role-publication.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
K, E, A, P, J = T.K, T.E, T.A, T.P, T.P.J
Q, S = T.R.Q, T.S
PARENT_ENTRIES = {'runtime', 'config', 'allocation.json'}


def _budget(roles):
    import resource
    payload = sum(len(tree['directories']) + len(tree['files']) + len(tree['links']) for tree in roles.values())
    required = len(os.listdir('/proc/self/fd')) + payload + 3 + 3 + 1 + 1 + 5 + 8
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot retain independent roles and metadata')
    return {'independentRoleDescriptorLimit': soft, 'independentRoleDescriptorUpperBound': required}


def _directory(stack, parent, name, mode):
    before = Q._directory(os.stat(name, dir_fd=parent, follow_symlinks=False), mode)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, fd)
    if Q._directory(os.fstat(fd), mode) != before:
        raise ValueError('Original independently observed directory required')
    return {'fd': fd, 'parent': parent, 'name': name, 'mode': mode, 'identity': before}


def _check_directory(entry):
    for info in (os.fstat(entry['fd']), os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)):
        if Q._directory(info, entry['mode']) != entry['identity']:
            raise ValueError('Independent directory namespace changed')


def _regular(stack, parent, name, mode, pair=None):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    maximum = J.LIMIT if pair is None else pair[0]
    if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
            or stat.S_IMODE(before.st_mode) != mode or not 0 <= before.st_size <= maximum
            or (pair is None and before.st_size == 0)):
        raise ValueError('Bounded single-link independently observed regular file required')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    stack.callback(os.close, fd)
    entry = {'parent': parent, 'name': name, 'fd': fd, 'identity': A._identity(before), 'mode': mode}
    data = _read_regular(entry, pair)
    entry['pair'] = len(data), E._sha(data)
    return entry, data


def _read_regular(entry, pair):
    def identity():
        for info in (os.fstat(entry['fd']), os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                    or stat.S_IMODE(info.st_mode) != entry['mode'] or A._identity(info) != entry['identity']):
                raise ValueError('Independent original regular inode required')
    identity()
    data = A._read(entry['fd'], J.LIMIT if pair is None else pair[0])
    if pair is not None and (len(data), E._sha(data)) != pair:
        raise ValueError('Independent payload differs from fresh original layout')
    identity()
    return data


def _allocation(raw, layout, parent_info):
    value = json.loads(raw.decode('ascii'))
    if (type(value) is not dict or set(value) != {'version', 'kind', 'transaction', 'planSha256', 'parent', 'roles', 'scope', 'recoveryAuthorized'}
            or J._encode(value) != raw or type(value['version']) is not int or value['version'] != 1
            or value['kind'] != 'private-created-publication-allocation'
            or type(value['transaction']) is not str or not re.fullmatch('[a-f0-9]{32}', value['transaction'])
            or value['planSha256'] != E._sha(layout) or value['scope'] != 'private-current-session-only'
            or value['recoveryAuthorized'] is not False or type(value['roles']) is not list or len(value['roles']) != 2):
        raise ValueError('Closed private allocation description matching fresh layout required')

    def node(row, fields):
        if type(row) is not dict or set(row) != fields:
            raise ValueError('Closed allocation identity observation required')
        if type(row['device']) is not int or row['device'] < 0 or type(row['inode']) is not int or row['inode'] <= 0:
            raise ValueError('Canonical observed device/inode numbers required')

    node(value['parent'], {'device', 'inode'})
    if (value['parent']['device'], value['parent']['inode']) != (parent_info.st_dev, parent_info.st_ino):
        raise ValueError('Allocation parent description differs from observed namespace')
    result = {}
    for role, row in zip(P.ROLES, value['roles']):
        node(row, {'role', 'createdName', 'device', 'inode'})
        if row['role'] != role or row['createdName'] != '.' + role + '-attempt-' + value['transaction']:
            raise ValueError('Exact ordered role creation descriptions required')
        result[role] = row['device'], row['inode']
    return result


def _journal(stack, parent, allocation_sha):
    _, parent_check, parent_fd = J._hold_parent(stack, parent)
    S._entries(parent_fd, {'journal'})
    directory = _directory(stack, parent_fd, 'journal', 0o700)
    names = {f'{i:04d}.json' for i in range(5)}
    S._entries(directory['fd'], names)
    records, raw = {}, []
    for name in sorted(names):
        entry, data = _regular(stack, directory['fd'], name, 0o600)
        records[name] = entry
        raw.append(data)
    progress = J._parse(tuple(raw))
    if (progress['lastPhase'] != 'config-published'
            or any(json.loads(data)['planSha256'] != allocation_sha for data in raw)):
        raise ValueError('Complete private journal prefix bound to allocation required')

    def check():
        parent_check()
        _check_directory(directory)
        S._entries(parent_fd, {'journal'})
        S._entries(directory['fd'], names)
        for entry in records.values():
            _read_regular(entry, entry['pair'])
        _check_directory(directory)
        parent_check()
    check()
    return check, progress


def _role(stack, parent, role, tree, described):
    root = _directory(stack, parent, role, 0o700)
    if root['identity'][:2] != described:
        raise ValueError('Observed role differs from allocation description')
    directories, files, links = {'': root}, {}, {}
    for name, mode in sorted(tree['directories'].items(), key=lambda item: (item[0].count('/'), item[0])):
        if name:
            group, _, leaf = name.rpartition('/')
            directories[name] = _directory(stack, directories[group]['fd'], leaf, mode)
    inventories = {name: set() for name in directories}
    for name in (*tree['files'], *tree['links'], *(name for name in directories if name)):
        group, _, leaf = name.rpartition('/')
        inventories[group].add(leaf)
    for name, entry in directories.items():
        _check_directory(entry)
        S._entries(entry['fd'], inventories[name])
    for name, (size, pin, mode) in tree['files'].items():
        group, _, leaf = name.rpartition('/')
        files[name], _ = _regular(stack, directories[group]['fd'], leaf, mode, (size, pin))
    for name, target in tree['links'].items():
        group, _, leaf = name.rpartition('/')
        directory = directories[group]['fd']
        before = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        fd = os.open(leaf, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
        stack.callback(os.close, fd)
        links[name] = directory, leaf, fd, before, target

    def check():
        for name, entry in directories.items():
            _check_directory(entry)
            S._entries(entry['fd'], inventories[name])
        for entry in files.values():
            _read_regular(entry, entry['pair'])
        for directory, leaf, fd, before, target in links.values():
            for after_read in (False, True):
                if after_read and os.readlink(leaf, dir_fd=directory) != target:
                    raise ValueError('Independent direct library alias text changed')
                for info in (os.fstat(fd), os.stat(leaf, dir_fd=directory, follow_symlinks=False)):
                    if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                            or stat.S_IMODE(info.st_mode) != 0o777 or A._identity(info) != A._identity(before)):
                        raise ValueError('Independent original alias inode required')
        for entry in directories.values():
            _check_directory(entry)
    check()
    return check


def hold_independent_roles(stack, user, source, recipes, parent, journal_parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    paths = A._canonical(source), A._canonical(recipes), P._parent_path(parent), J._path(journal_parent)
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('Disjoint originals and private inspection parents required')
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    roles = T._roles(layout)
    budget = _budget(roles)
    parent_fd, parent_check = P._hold_parent(stack, paths[2])
    S._entries(parent_fd, PARENT_ENTRIES)
    allocation, raw_allocation = _regular(stack, parent_fd, 'allocation.json', 0o600)
    described = _allocation(raw_allocation, layout, os.fstat(parent_fd))
    journal_check, progress = _journal(stack, journal_parent, E._sha(raw_allocation))
    role_checks = [_role(stack, parent_fd, role, roles[role], described[role]) for role in P.ROLES]

    def check():
        original['check']()
        parent_check()
        S._entries(parent_fd, PARENT_ENTRIES)
        _read_regular(allocation, allocation['pair'])
        journal_check()
        for role_check in role_checks:
            role_check()
        journal_check()
        parent_check()
        S._entries(parent_fd, PARENT_ENTRIES)
        _read_regular(allocation, allocation['pair'])
        original['check']()

    def observe():
        check()
        return {**original['policy'], **progress, **budget, 'kind': 'independent-private-role-inspection',
            'independentRetainedRoleObserverVerified': True, 'readOnlyPrivateInspection': True,
            'privateRegularFilesVerified': 261, 'privateAliasesVerified': 4,
            'finalLayoutSha256': E._sha(layout), 'allocationSha256': E._sha(raw_allocation),
            'storedReceiptAdopted': False, 'createdInodeOwnershipVerified': False,
            'privateTargetOwnershipVerified': False, 'restartRecoveryVerified': False,
            'crossFilesystemPublicationVerified': False, 'artifactBytesCopied': False,
            **dict.fromkeys(J.FALSE_FLAGS, False)}
    observe()
    return {'check': check, 'observe': observe}


def main(argv=None):
    raise ValueError('No installation, publication, adoption or recovery CLI exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No independent-role CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
