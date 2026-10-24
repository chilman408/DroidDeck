# SPDX-License-Identifier: GPL-3.0-only
"""Read-only final-mode tree verification below a private /root parent."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('prepared_mapped_layout', Path(__file__).with_name('runtime-mapped-install-stage.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
K, E, A, S = M.K, M.E, M.A, M.S


def _spec(raw):
    records, _ = M._layout(raw)
    value = json.loads(raw)
    files = {row['path'][1:]: (row['size'], row['sha256'], row['mode']) for row in value['files']}
    files['final-layout.json'] = (*records['final-layout.json'], 0o600)
    links = {row['path'][1:]: row['target'] for row in value['links']}
    folders = {'': 0o700}
    names = set(files) | set(links)
    for name in names:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                folders[str(parent)] = 0o700 if str(parent) in ('opt/droiddeck/guest', 'opt/droiddeck/guest/bin') else 0o755
    if (len(files) != 260 or len(links) != 4 or set(files).intersection(links)
            or names.intersection(folders)):
        raise ValueError('Disjoint prepared files, directories and direct aliases required')
    for name, target in links.items():
        if (PurePosixPath(target).name != target or target in ('.', '..')
                or str(PurePosixPath(name).parent / target) not in files):
            raise ValueError('Reviewed aliases must point directly to planned regular libraries')
    expected_dirs = [{'path': '/' + name, 'mode': mode, 'uid': 0, 'gid': 0}
        for name, mode in sorted(folders.items()) if any('/' + name == root or ('/' + name).startswith(root + '/') for root in K.ROOTS)]
    if (value['ownedDirectories'] != expected_dirs
            or any(type(row['mode']) is not int or type(row['uid']) is not int or type(row['gid']) is not int
                   for row in value['ownedDirectories'])):
        raise ValueError('Exact reviewed owned-directory permissions required')
    manifest = E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'prepared-install-tree',
        'finalLayoutSha256': E._sha(raw), 'installable': False, 'readyForActivation': False,
        'files': [{'path': name, 'size': size, 'sha256': pin, 'mode': mode, 'uid': 0, 'gid': 0}
                  for name, (size, pin, mode) in sorted(files.items())],
        'directories': [{'path': name, 'mode': mode, 'uid': 0, 'gid': 0} for name, mode in sorted(folders.items())],
        'links': [{'path': name, 'target': target, 'uid': 0, 'gid': 0} for name, target in sorted(links.items())]}, compact=True)
    files[A.MANIFEST] = len(manifest), E._sha(manifest), 0o600
    return {'files': MappingProxyType(files), 'directories': MappingProxyType(folders),
            'links': MappingProxyType(links), 'manifest': manifest}


def _parent(parent):
    parent = A._canonical(parent)
    if parent == PurePosixPath('/root') or not parent.is_relative_to(PurePosixPath('/root')):
        raise ValueError('Private prepared parent strictly below /root required')
    return parent


def _budget(root, tree):
    import resource
    # One independent verifier: ancestor chain, interior directories, files and O_PATH aliases.
    required = (len(os.listdir('/proc/self/fd')) + len(root.parts)
                + len(tree['directories']) - 1 + len(tree['files']) + len(tree['links']) + 8)
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot hold prepared tree verification')
    return {'preparedVerifierDescriptorLimit': soft, 'preparedVerifierDescriptorUpperBound': required}


def _directory(info, mode=None):
    identity = S._directory(info, False)
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('Exact original prepared directory mode required')
    return identity


def _hold_tree(stack, parent, tree):
    """Internal observer only; the public composition supplies fresh original authority."""
    A._root_linux()
    parent = _parent(parent)
    root = parent / 'prepared'
    budget = _budget(root, tree)
    chain, directories, files, links = [], {}, {}, {}
    current = None

    def opened(ancestor, name, mode):
        before = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
        identity = _directory(before, mode)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ancestor)
        stack.callback(os.close, fd)
        if _directory(os.fstat(fd), mode) != identity:
            raise ValueError('Original prepared directory generation required')
        chain.append((ancestor, name, fd, mode, identity))
        return fd

    for index, part in enumerate(root.parts):
        current = opened(current, part, 0o700 if index >= len(root.parts) - 2 else None)
    parent_fd = chain[-2][2]
    directories[''] = current
    S._entries(parent_fd, {'prepared'})
    for name, mode in sorted(tree['directories'].items(), key=lambda item: (item[0].count('/'), item[0])):
        if name:
            group, _, leaf = name.rpartition('/')
            directories[name] = opened(directories[group], leaf, mode)
    inventory = {name: set() for name in directories}
    for name in (*tree['files'], *tree['links'], *(name for name in directories if name)):
        group, _, leaf = name.rpartition('/')
        inventory[group].add(leaf)

    def regular(entry):
        directory, name, fd, identity, pair, mode = entry
        for info in (os.fstat(fd), os.stat(name, dir_fd=directory, follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                    or stat.S_IMODE(info.st_mode) != mode or A._identity(info) != identity):
                raise ValueError('Original single-link prepared regular file required')

    def read_file(entry):
        regular(entry)
        data = A._read(entry[2], entry[4][0])
        if (len(data), E._sha(data)) != entry[4]:
            raise ValueError('Prepared file differs from original authority')
        regular(entry)
        return data

    for name, (size, pin, mode) in sorted(tree['files'].items()):
        group, _, leaf = name.rpartition('/')
        directory = directories[group]
        before = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError('Exact bounded prepared regular file required')
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        entry = directory, leaf, fd, A._identity(before), (size, pin), mode
        read_file(entry)
        files[name] = entry
    for name, target in sorted(tree['links'].items()):
        group, _, leaf = name.rpartition('/')
        directory = directories[group]
        fd = os.open(leaf, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
        stack.callback(os.close, fd)
        links[name] = directory, leaf, fd, A._identity(os.fstat(fd)), target

    def check():
        A._root_linux()
        for ancestor, name, fd, mode, identity in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if _directory(info, mode) != identity:
                    raise ValueError('Original prepared directory namespace required')
        S._entries(parent_fd, {'prepared'})
        for name, fd in directories.items():
            S._entries(fd, inventory[name])
        for entry in files.values():
            read_file(entry)
        for name, (directory, leaf, fd, identity, target) in links.items():
            for after_read in (False, True):
                if after_read and os.readlink(leaf, dir_fd=directory) != target:
                    raise ValueError('Exact direct prepared library alias required')
                for info in (os.fstat(fd), os.stat(leaf, dir_fd=directory, follow_symlinks=False)):
                    if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                            or stat.S_IMODE(info.st_mode) != 0o777 or A._identity(info) != identity):
                        raise ValueError('Original root-owned prepared alias inode required')
            regular(files[str(PurePosixPath(name).parent / target)])

    check()
    return {'check': check, 'files': tuple(sorted(files)), 'links': tuple(sorted(links)),
            '_read_file': lambda name: read_file(files[name]), 'budget': budget}


def hold_prepared_install_tree(stack, user, source, recipes, parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    parent = _parent(parent)
    source, recipes = map(A._canonical, (source, recipes))
    roots = source, recipes, parent
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for i, first in enumerate(roots) for second in roots[i + 1:]):
        raise ValueError('Disjoint original inputs and prepared tree required')
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    raw_layout = original['read_plan']()
    tree = _spec(raw_layout)
    original['check']()
    held = _hold_tree(stack, parent, tree)

    def check():
        original['check']()
        held['check']()
        original['check']()

    def read(name):
        if type(name) is not str or name not in tree['files']:
            raise ValueError('Closed prepared regular file read required')
        check()
        data = held['_read_file'](name)
        check()
        return data

    def read_link(name):
        if type(name) is not str or name not in tree['links']:
            raise ValueError('Closed prepared alias read required')
        check()
        target = tree['links'][name]
        check()
        return target

    check()
    return {'check': check, 'read': read, 'read_link': read_link, 'files': held['files'], 'links': held['links'],
        'policy': {**original['policy'], 'kind': 'observed-private-prepared-install-tree',
            'preparedFileModesVerified': True, 'preparedLibraryAliasesVerified': True,
            'preparedRegularFilesVerified': 261, 'preparedAliasesVerified': 4,
            'preparedTreeManifestSha256': E._sha(tree['manifest']), **held['budget'],
            **dict.fromkeys(('createdInodeOwnershipVerified', 'installationAuthorized', 'recoveryAuthorized',
                'finalPermissionsApplied', 'runtimeLinksMaterialized', 'finalTargetsPublished',
                'servicesRegistered', 'servicesActivated', 'installable', 'readyForActivation'), False)}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--inspect-prepared-tree' or argv[5] != '-':
        raise ValueError('Only read-only original-bound prepared tree inspection is exposed')
    with ExitStack() as stack:
        held = hold_prepared_install_tree(stack, argv[1], argv[2], argv[3], argv[4], sys.stdin.buffer.read(E.P.C.LIMIT + 1))
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, E.P.configparser.Error, M.subprocess.SubprocessError):
        print('Prepared tree inspection refused; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
