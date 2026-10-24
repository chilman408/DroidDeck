# SPDX-License-Identifier: GPL-3.0-only
"""Durable code-pair rollback primitive; private Linux fixtures, never live apply."""
from contextlib import ExitStack
import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys

spec = importlib.util.spec_from_file_location('exchange_package', Path(__file__).with_name('update-package.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
ROLES = tuple(sorted(P.COMPONENTS))
LIMIT = 8 * 1024 * 1024


def generation(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def directory(info, mode):
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, mode)):
        raise ValueError('Original protected code directory required')
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]


def regular(info, mode, maximum):
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, mode)
            or not 0 <= info.st_size <= maximum):
        raise ValueError('Bounded original single-link code file required')
    return generation(info)


def root_linux():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux private exchange fixture required')


def hold(stack, path):
    root_linux()
    path = Path(path)
    if (not path.is_absolute() or path.parent != Path('/root') or str(path) != str(Path(os.path.normpath(path)))
            or not re.fullmatch('droiddeck-update-exchange-fixture-[A-Za-z0-9_-]{1,80}', path.name)):
        raise ValueError('Only a new private exchange fixture namespace is admitted')
    chain, parent = [], None
    for index, name in enumerate(path.parts):
        mode = 0o700 if index else 0o755
        before = directory(os.stat(name, dir_fd=parent, follow_symlinks=False), mode)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if directory(os.fstat(fd), mode) != before:
            raise ValueError('Original fixture ancestor required')
        chain.append((parent, name, fd, before, mode))
        parent = fd

    def check():
        for ancestor, name, fd, identity, mode in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if directory(info, mode) != identity:
                    raise ValueError('Original fixture namespace changed')
    check()
    return parent, check


def entries(fd, expected):
    if set(os.listdir(fd)) != set(expected):
        raise ValueError('Exact closed exchange namespace required')


def read(parent, name, maximum=LIMIT):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = regular(os.fstat(fd), 0o600, maximum)
        chunks, remaining = [], before[6]
        while remaining:
            raw = os.read(fd, min(remaining, 1048576))
            if not raw:
                raise ValueError('Complete exchange journal required')
            chunks.append(raw)
            remaining -= len(raw)
        if (regular(os.fstat(fd), 0o600, maximum) != before
                or generation(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before):
            raise ValueError('Original journal generation changed')
        return b''.join(chunks), before
    finally:
        os.close(fd)


def write(parent, name, value):
    raw = P.encode(value)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError('Incomplete exchange record')
            view = view[count:]
        os.fsync(fd)
        os.fsync(parent)
    finally:
        os.close(fd)


def tree(parent, name, rows):
    expected = P.inventory(rows)
    directories = {'': []}
    for relative in expected:
        for ancestor in Path(relative).parents:
            if str(ancestor) != '.':
                directories[ancestor.as_posix()] = []
    for relative in (*expected, *(name for name in directories if name)):
        group, _, leaf = relative.rpartition('/')
        directories[group].append(leaf)
    result = {'directories': {}, 'files': {}}
    with ExitStack() as stack:
        held, files = {}, {}
        for relative in sorted(directories, key=lambda value: (value.count('/'), value)):
            group, _, leaf = relative.rpartition('/')
            owner = parent if not relative else held[group]
            leaf = name if not relative else leaf
            before = directory(os.stat(leaf, dir_fd=owner, follow_symlinks=False), 0o755)
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=owner)
            stack.callback(os.close, fd)
            if directory(os.fstat(fd), 0o755) != before:
                raise ValueError('Original code subtree required')
            held[relative] = fd
            result['directories'][relative] = before
            entries(fd, directories[relative])
        for relative, row in expected.items():
            group, _, leaf = relative.rpartition('/')
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=held[group])
            stack.callback(os.close, fd)
            before = regular(os.fstat(fd), row['mode'], P.MAX_FILE)
            digest, remaining = hashlib.sha256(), before[6]
            if remaining != row['size']:
                raise ValueError('Original code size required')
            while remaining:
                raw = os.read(fd, min(remaining, 1048576))
                if not raw:
                    raise ValueError('Complete code bytes required')
                digest.update(raw); remaining -= len(raw)
            if digest.hexdigest() != row['sha256']:
                raise ValueError('Original code digest required')
            for info in (os.fstat(fd), os.stat(leaf, dir_fd=held[group], follow_symlinks=False)):
                if regular(info, row['mode'], P.MAX_FILE) != before:
                    raise ValueError('Original code-file generation required')
            result['files'][relative] = before
            files[relative] = fd
        for relative, fd in files.items():
            group, _, leaf = relative.rpartition('/')
            for info in (os.fstat(fd), os.stat(leaf, dir_fd=held[group], follow_symlinks=False)):
                if generation(info) != result['files'][relative]:
                    raise ValueError('Code file changed during complete tree inspection')
        for relative, fd in held.items():
            if directory(os.fstat(fd), 0o755) != result['directories'][relative]:
                raise ValueError('Original subtree namespace changed')
            entries(fd, directories[relative])
        if directory(os.stat(name, dir_fd=parent, follow_symlinks=False), 0o755) != result['directories']['']:
            raise ValueError('Code root changed during inspection')
    return result


def exchange(left, right, role):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(left, role.encode('ascii'), right, role.encode('ascii'), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    os.fsync(left)
    os.fsync(right)


class Transaction:
    def __init__(self, stack, path):
        self.root, self.check = hold(stack, path)
        self.parents, self.identities = {}, {}
        for name in ('installed', 'prepared', 'journal'):
            before = directory(os.stat(name, dir_fd=self.root, follow_symlinks=False), 0o700)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root)
            stack.callback(os.close, fd)
            if directory(os.fstat(fd), 0o700) != before:
                raise ValueError('Original private role parent required')
            self.parents[name], self.identities[name] = fd, before
        import fcntl
        fcntl.flock(self.parents['journal'], fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw, self.original_generation = read(self.parents['journal'], 'original.json')
        self.original, self.pin = P.document(raw), P.sha(raw)
        value = self.original
        if (type(value) is not dict or set(value) != {'schema', 'kind', 'rootIdentity', 'parentIdentities', 'roles', 'components'}
                or type(value['schema']) is not int or value['schema'] != 1 or value['kind'] != 'private-code-exchange'
                or value['rootIdentity'] != directory(os.fstat(self.root), 0o700)
                or value['parentIdentities'] != self.identities):
            raise ValueError('Original durable exchange ownership required')
        self.components = components(value['components'])
        if type(value['roles']) is not dict or set(value['roles']) != set(ROLES):
            raise ValueError('All original code-pair identities required')
        self.records = {'original.json': self.original_generation}
        ready, ready_info = read(self.parents['journal'], 'ready.json')
        if P.document(ready) != {'schema': 1, 'originalSha256': self.pin, 'originalGeneration': self.original_generation}:
            raise ValueError('Original completed preparation required; no partial adoption')
        self.records['ready.json'] = ready_info
        self.observe()

    def namespace(self):
        self.check()
        entries(self.root, self.parents)
        for name, fd in self.parents.items():
            for info in (os.fstat(fd), os.stat(name, dir_fd=self.root, follow_symlinks=False)):
                if directory(info, 0o700) != self.identities[name]:
                    raise ValueError('Retained original role parent changed')
            if name != 'journal':
                entries(fd, ROLES)
        for name, before in self.records.items():
            _raw, current = read(self.parents['journal'], name)
            if current != before:
                raise ValueError('Previously observed exchange record replaced')

    def marker(self, name, *, create=False):
        expected = {'schema': 1, 'originalSha256': self.pin, 'phase': name}
        fd = self.parents['journal']
        if create:
            self.namespace()
            write(fd, name + '.json', expected)
        raw, info = read(fd, name + '.json')
        if P.document(raw) != expected:
            raise ValueError('Original exact exchange phase required')
        previous = self.records.setdefault(name + '.json', info)
        if previous != info:
            raise ValueError('Original observed phase generation required')

    def observe(self):
        self.namespace()
        journal = set(os.listdir(self.parents['journal']))
        allowed = {'original.json', 'ready.json', 'rollback-intent.json', 'rolled-back.json'}
        allowed.update(phase + '-' + role + '.json' for role in ROLES for phase in ('apply-intent', 'apply-done'))
        if not journal <= allowed or not {'original.json', 'ready.json'} <= journal:
            raise ValueError('Closed completed exchange journal required')
        for name in sorted(journal - {'original.json', 'ready.json'}):
            self.marker(name[:-5])
        orientations, unfinished = {}, False
        for role in ROLES:
            row = self.components[role]
            originals = self.original['roles'][role]
            if type(originals) is not dict or set(originals) != {'before', 'after'}:
                raise ValueError('Closed original code pair required')
            current = directory(os.stat(role, dir_fd=self.parents['installed'], follow_symlinks=False), 0o755)
            if current == originals['before']['directories']['']:
                selected = ('before', 'after')
            elif current == originals['after']['directories']['']:
                selected = ('after', 'before')
            else:
                raise ValueError('Foreign current code directory; never adopt')
            for parent, side in zip(('installed', 'prepared'), selected):
                if tree(self.parents[parent], role, row[side]) != originals[side]:
                    raise ValueError('Exact retained code-pair generations required')
            intent, done = ('apply-intent-' + role + '.json' in journal, 'apply-done-' + role + '.json' in journal)
            if done and not intent or (intent and unfinished):
                raise ValueError('Ordered original apply intent required')
            if not done:
                unfinished = True
            if selected[0] == 'after' and not intent:
                raise ValueError('No exchange without original durable intent')
            if 'rollback-intent.json' not in journal and done and selected[0] != 'after':
                raise ValueError('Completed apply cannot silently restore code')
            orientations[role] = selected[0]
        if 'rolled-back.json' in journal and ('rollback-intent.json' not in journal or set(orientations.values()) != {'before'}):
            raise ValueError('Completed original rollback must contain only original code')
        self.namespace()
        return {'kind': 'private-code-exchange-observation', 'orientation': orientations,
                'rollbackComplete': 'rolled-back.json' in journal, 'livePublicationPerformed': False,
                'installationAuthorized': False, 'readyForApply': False, 'servicesChanged': False}

    def apply(self, guard, *, checkpoint=lambda phase, role: None):
        result = self.observe()
        if (set(os.listdir(self.parents['journal'])) != {'original.json', 'ready.json'}
                or set(result['orientation'].values()) != {'before'}):
            raise ValueError('Fresh prepared exchange only; interrupted apply must roll back')
        for role in ROLES:
            guard(); self.observe()
            self.marker('apply-intent-' + role, create=True)
            checkpoint('intent', role)
            guard(); self.observe()
            exchange(self.parents['installed'], self.parents['prepared'], role)
            checkpoint('exchanged', role)
            guard(); self.observe()
            self.marker('apply-done-' + role, create=True)
            checkpoint('done', role)
        return self.observe()

    def rollback(self, guard, *, checkpoint=lambda phase, role: None):
        result = self.observe()
        if result['rollbackComplete']:
            return result
        guard()
        if 'rollback-intent.json' not in os.listdir(self.parents['journal']):
            self.marker('rollback-intent', create=True)
        for role in reversed(ROLES):
            guard(); result = self.observe()
            if result['orientation'][role] == 'after':
                checkpoint('restore-intent', role)
                guard(); self.observe()
                exchange(self.parents['installed'], self.parents['prepared'], role)
                checkpoint('restored', role)
                guard(); self.observe()
        self.marker('rolled-back', create=True)
        return self.observe()


def components(value):
    if (type(value) is not list or len(value) != len(ROLES)
            or any(type(row) is not dict or set(row) != {'name', 'before', 'after'} for row in value)
            or [row['name'] for row in value] != list(ROLES)):
        raise ValueError('Closed sorted fixed component plan required')
    for row in value:
        if P.inventory(row['before']) == P.inventory(row['after']):
            raise ValueError('Actual code replacement required')
    return {row['name']: row for row in value}


def prepare(path, value):
    selected = components(value)
    with ExitStack() as stack:
        root, check = hold(stack, path)
        entries(root, ('installed', 'prepared'))
        parents, identities = {}, {}
        for name in ('installed', 'prepared'):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            stack.callback(os.close, fd)
            parents[name], identities[name] = fd, directory(os.fstat(fd), 0o700)
            entries(fd, ROLES)
        roles = {role: {side: tree(parents[parent], role, selected[role][side])
                       for parent, side in (('installed', 'before'), ('prepared', 'after'))} for role in ROLES}
        roots = [data[side]['directories'][''][:2] for data in roles.values() for side in ('before', 'after')]
        if len({tuple(pair) for pair in roots}) != 2 * len(ROLES) or len({pair[0] for pair in roots}) != 1:
            raise ValueError('Disjoint original same-filesystem code pairs required')
        check()
        os.mkdir('journal', 0o700, dir_fd=root); os.fsync(root)
        fd = os.open('journal', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, fd)
        identities['journal'] = directory(os.fstat(fd), 0o700)
        write(fd, 'original.json', {'schema': 1, 'kind': 'private-code-exchange',
            'rootIdentity': directory(os.fstat(root), 0o700), 'parentIdentities': identities,
            'roles': roles, 'components': value})
        raw, generation = read(fd, 'original.json')
        write(fd, 'ready.json', {'schema': 1, 'originalSha256': P.sha(raw), 'originalGeneration': generation})
        check()
    with ExitStack() as stack:
        return Transaction(stack, path).observe()


if __name__ == '__main__':
    print('No live apply or recovery CLI is exposed', file=sys.stderr)
    sys.exit(1)
