# SPDX-License-Identifier: GPL-3.0-only
"""Exclusive new private prepared tree; never a live /opt or /etc install."""
from contextlib import ExitStack
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys

spec = importlib.util.spec_from_file_location('staged_prepared_observer', Path(__file__).with_name('runtime-prepared-tree.py'))
Q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Q)
K, M, E, A, S = Q.K, Q.M, Q.E, Q.A, Q.S


def _budget(parent, tree):
    import resource
    one_tree = len((parent / 'prepared').parts) + len(tree['directories']) - 1 + len(tree['files']) + len(tree['links'])
    required = len(os.listdir('/proc/self/fd')) + 2 * one_tree + 16
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot retain both prepared trees')
    return {'preparedPublicationDescriptorLimit': soft, 'preparedPublicationDescriptorUpperBound': required}


def _rename_new(directory, attempt):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(directory, attempt.encode('ascii'), directory, b'prepared', 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _file(entry):
    for info in (os.fstat(entry['fd']), os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)):
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(info.st_mode) != entry['mode'] or A._identity(info) != A._identity(entry['info'])):
            raise ValueError('Original exclusively created prepared file required')


def _directory(entry):
    for info in (os.fstat(entry['fd']), os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)):
        if Q._directory(info, entry['mode']) != Q._directory(entry['info'], entry['mode']):
            raise ValueError('Original prepared publication directory required')


def _chmod_created(entry, mode, *, directory=False):
    check = _directory if directory else _file
    check(entry)
    if mode != entry['mode']:
        before = entry['info']
        os.fchmod(entry['fd'], mode)
        after = os.fstat(entry['fd'])
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid') if directory else (
            'st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns')
        if any(getattr(before, name) != getattr(after, name) for name in fields) or stat.S_IMODE(after.st_mode) != mode:
            raise ValueError('Only reviewed mode change on original created inode allowed')
        if not directory:
            named = os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)
            if A._identity(named) != A._identity(after):
                raise ValueError('Created file replaced during mode transition')
        entry.update(info=after, mode=mode)
        check(entry)
        os.fsync(entry['fd'])


def publish_prepared_install_tree(stack, user, source, recipes, parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    source, recipes, parent = A._canonical(source), A._canonical(recipes), Q._parent(parent)
    roots = source, recipes, parent
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for i, first in enumerate(roots) for second in roots[i + 1:]):
        raise ValueError('Disjoint original inputs and new prepared parent required')
    M._preflight_parent(parent)
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    tree = Q._spec(layout)
    budget = _budget(parent, tree)
    if sum(size for size, _, _ in tree['files'].values()) > 512 * 1024 * 1024:
        raise ValueError('Bounded prepared publication required')
    payload = {}
    for name, (size, pin, _) in tree['files'].items():
        original['check']()
        data = (layout if name == 'final-layout.json' else tree['manifest'] if name == A.MANIFEST
                else original['read_file']('/' + name))
        if (len(data), E._sha(data)) != (size, pin):
            raise ValueError('Prepared source differs from original authority')
        original['check']()
        payload[name] = data

    chain, directories, inventories, files, links = [], {}, {}, {}, {}
    parent_entries = set()
    current = None

    def opened(ancestor, name, mode):
        before = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
        identity = Q._directory(before, mode)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ancestor)
        stack.callback(os.close, fd)
        if Q._directory(os.fstat(fd), mode) != identity:
            raise ValueError('Original new prepared directory generation required')
        entry = {'parent': ancestor, 'name': name, 'fd': fd, 'info': before, 'mode': mode}
        chain.append(entry)
        return entry

    for index, part in enumerate(parent.parts):
        current = opened(None if current is None else current['fd'], part, 0o700 if index == len(parent.parts) - 1 else None)
    parent_fd = current['fd']

    def check_created():
        A._root_linux()
        for entry in chain:
            _directory(entry)
        S._entries(parent_fd, parent_entries)
        for name, entry in directories.items():
            S._entries(entry['fd'], inventories[name])
        for name, entry in files.items():
            _file(entry)
            if A._read(entry['fd'], len(payload[name])) != payload[name]:
                raise ValueError('Created prepared bytes changed')
            _file(entry)
        for entry in links.values():
            for after_read in (False, True):
                if after_read and os.readlink(entry['name'], dir_fd=entry['parent']) != entry['target']:
                    raise ValueError('Created direct library alias changed')
                for info in (os.fstat(entry['fd']), os.stat(entry['name'], dir_fd=entry['parent'], follow_symlinks=False)):
                    if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                            or stat.S_IMODE(info.st_mode) != 0o777 or A._identity(info) != A._identity(entry['info'])):
                        raise ValueError('Original created prepared alias required')

    def guard():
        original['check']()
        check_created()

    # Reopen and readmit the empty parent after all original source reads.
    guard()
    space = os.fstatvfs(parent_fd)
    if space.f_bavail * space.f_frsize < sum(map(len, payload.values())) + 1024 * 1024:
        raise ValueError('Insufficient prepared publication space')
    attempt = '.prepared-attempt-' + secrets.token_hex(16)
    guard()
    try:
        os.mkdir(attempt, 0o700, dir_fd=parent_fd)
        parent_entries.add(attempt)
        directories[''] = opened(parent_fd, attempt, 0o700)
        inventories[''] = set()
        groups = sorted((name for name in tree['directories'] if name), key=lambda name: (name.count('/'), name))
        for name in groups:
            guard()
            group, _, leaf = name.rpartition('/')
            ancestor = directories[group]['fd']
            os.mkdir(leaf, 0o700, dir_fd=ancestor)
            entry = opened(ancestor, leaf, 0o700)
            directories[name], inventories[name] = entry, set()
            inventories[group].add(leaf)
            _chmod_created(entry, tree['directories'][name], directory=True)
            guard()

        def copied(name):
            guard()
            group, _, leaf = name.rpartition('/')
            directory = directories[group]['fd']
            fd, info, _ = S._write_private(stack, directory, leaf, payload[name])
            entry = {'parent': directory, 'name': leaf, 'fd': fd, 'info': info, 'mode': 0o600}
            files[name] = entry
            inventories[group].add(leaf)
            _chmod_created(entry, tree['files'][name][2])
            guard()

        for name in sorted(name for name in payload if name != A.MANIFEST):
            copied(name)
        for name, target in sorted(tree['links'].items()):
            guard()
            group, _, leaf = name.rpartition('/')
            directory = directories[group]['fd']
            os.symlink(target, leaf, dir_fd=directory)
            before = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            fd = os.open(leaf, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, fd)
            if A._identity(os.fstat(fd)) != A._identity(before):
                raise ValueError('New library alias replaced before pinning')
            links[name] = {'parent': directory, 'name': leaf, 'fd': fd, 'info': before, 'target': target}
            inventories[group].add(leaf)
            guard()
        for name in reversed(groups):
            os.fsync(directories[name]['fd'])
        copied(A.MANIFEST)
        os.fsync(directories['']['fd'])
        os.fsync(parent_fd)
        guard()
        _rename_new(parent_fd, attempt)
        # Rebind the retained created root immediately, before any fallible post-rename call.
        directories['']['name'] = 'prepared'
        parent_entries = {'prepared'}
        os.fsync(parent_fd)
        guard()
        observed = Q._hold_tree(stack, parent, tree)
        check_created()
        observed['check']()
        original['check']()
    except Exception as error:
        raise S.CandidatePublicationUncertain('Prepared attempt or tree may exist; preserve parent before retry') from error

    def check():
        try:
            original['check']()
            check_created()
            observed['check']()
            check_created()
            original['check']()
        except Exception as error:
            raise S.CandidatePublicationUncertain('Prepared ownership or source changed; preserve parent') from error

    def read(name):
        try:
            if type(name) is not str or name not in tree['files']:
                raise ValueError('Closed prepared regular read required')
            check()
            data = observed['_read_file'](name)
            check()
            return data
        except Exception as error:
            raise S.CandidatePublicationUncertain('Prepared read uncertain; preserve parent') from error

    def read_link(name):
        try:
            if type(name) is not str or name not in tree['links']:
                raise ValueError('Closed prepared alias read required')
            check()
            target = tree['links'][name]
            check()
            return target
        except Exception as error:
            raise S.CandidatePublicationUncertain('Prepared alias read uncertain; preserve parent') from error

    check()
    return {'check': check, 'read': read, 'read_link': read_link, 'files': observed['files'], 'links': observed['links'],
        'policy': {**original['policy'], **budget, **observed['budget'], 'kind': 'new-private-prepared-install-tree',
            'preparedPath': str(parent / 'prepared'), 'newPreparedTreePublished': True,
            'createdInodeOwnershipVerified': True, 'createdInodeOwnershipScope': 'new-private-prepared-tree-only',
            'preparedFileModesVerified': True, 'preparedLibraryAliasesVerified': True,
            'preparedRegularFilesVerified': 261, 'preparedAliasesVerified': 4,
            'preparedTreeManifestSha256': E._sha(tree['manifest']), 'parentPreflightContinuouslyHeld': False,
            'publisherParentIndependentlyReadmitted': True,
            **dict.fromkeys(('installationAuthorized', 'recoveryAuthorized', 'finalPermissionsApplied',
                'runtimeLinksMaterialized', 'finalTargetsPublished', 'servicesRegistered', 'servicesActivated',
                'installable', 'readyForActivation'), False)}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--stage-prepared-tree' or argv[5] != '-':
        raise ValueError('Only new private prepared tree staging is exposed')
    with ExitStack() as stack:
        held = publish_prepared_install_tree(stack, argv[1], argv[2], argv[3], argv[4], sys.stdin.buffer.read(E.P.C.LIMIT + 1))
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except S.CandidatePublicationUncertain:
        print('Prepared tree uncertain; preserve parent before retry; no installation performed', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, E.P.configparser.Error, M.subprocess.SubprocessError):
        print('Prepared tree staging refused; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
