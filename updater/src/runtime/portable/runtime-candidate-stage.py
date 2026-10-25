# SPDX-License-Identifier: GPL-3.0-only
"""Internal source-only publication into an independently prepared private parent."""
import ctypes
import importlib.util
import os
from pathlib import Path
import secrets
import stat
import sys

_spec = importlib.util.spec_from_file_location('runtime_candidate_admission', Path(__file__).with_name('runtime-candidate-admission.py'))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
PUBLISHED_NAME = 'candidate'


class CandidatePublicationUncertain(RuntimeError):
    """A new attempt or final directory may exist; preserve it, never retry blindly."""


def _entries(fd, expected):
    seen = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in seen:
                raise ValueError('Closed private publication inventory required')
            seen.add(entry.name)
    if seen != expected:
        raise ValueError('Exact private publication inventory required')


def _directory(info, private):
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022
            or (private and stat.S_IMODE(info.st_mode) != 0o700)):
        raise ValueError('Original root-owned protected publication directory required')
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _write_private(stack, directory, name, raw):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    stack.callback(os.close, fd)
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Incomplete candidate write')
        view = view[count:]
    os.fsync(fd)
    return fd, os.fstat(fd), raw


def _rename_new(directory, attempt):
    return _rename_tree_new(directory, attempt, PUBLISHED_NAME)


def _rename_tree_new(directory, attempt, published):
    if published not in ('candidate', 'assembly'):
        raise ValueError('Fixed private publication name required')
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(directory, attempt.encode('ascii'), directory, published.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _paths(source, parent):
    A._root_linux()
    source, parent = A._canonical(source), A._canonical(parent)
    if source.is_relative_to(parent) or parent.is_relative_to(source):
        raise ValueError('Disjoint source and publication namespaces required')
    return source, parent


def publish_runtime_candidate(stack, source, parent, *, source_owner=(0, 0)):
    """Trusted caller supplies source owner and EMPTY root700 destination parent."""
    source, parent = _paths(source, parent)
    owner = A._owner(source_owner)
    package = A.hold_runtime_candidate(stack, source, owner=owner)
    return _publish_held(stack, package, parent, unprivileged=owner != (0, 0))


def publish_user_candidate(stack, user, source, parent):
    """Real NSS identity/home remain checked through publication, never caller UID/GID."""
    source, parent = _paths(source, parent)
    package = A.hold_user_candidate(stack, user, source)
    result = _publish_held(stack, package, parent, unprivileged=True)
    try:
        package['check']()
    except Exception as error:
        raise CandidatePublicationUncertain('Candidate may exist; source account changed before completion; preserve and inspect') from error
    return {**result, 'policy': {**result['policy'], 'sourceAccountMatched': True}}


def _publish_held(stack, package, parent, *, unprivileged):
    result = _publish_tree(stack, package, parent, published=PUBLISHED_NAME,
                           rename=_rename_new, verify=A.hold_runtime_candidate)
    return {**result, 'policy': {
        **result['policy'], 'newSourceCandidatePublished': True, 'rootPrivateCopyVerified': True,
        'unprivilegedSourceAdmitted': unprivileged}}


def _publish_tree(stack, package, parent, *, published, rename, verify):
    """Private mechanism; trusted wrappers fix the layout, destination name and verifier."""
    if published not in ('candidate', 'assembly'):
        raise ValueError('Fixed private publication name required')
    payload = {relative: package['read'](relative) for relative in package['files']}
    chain, directories, expected, files = [], {}, {}, {}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = None
    for index, part in enumerate(parent.parts):
        fd = os.open(part, flags, dir_fd=current)
        stack.callback(os.close, fd)
        private = index == len(parent.parts) - 1
        identity = _directory(os.fstat(fd), private)
        chain.append((current, part, fd, private, identity))
        current = fd
    parent_fd, parent_entries = current, set()

    def check_created():
        A._root_linux()
        for ancestor, leaf, fd, private, before in chain:
            for info in (os.fstat(fd), os.stat(leaf, dir_fd=ancestor, follow_symlinks=False)):
                if _directory(info, private) != before:
                    raise ValueError('Original publication directory generation required')
        _entries(parent_fd, parent_entries)
        for relative, fd in directories.items():
            _entries(fd, expected[relative])
        for directory, leaf, fd, original, raw in files.values():
            for after_read in (False, True):
                if after_read and A._read(fd, len(raw)) != raw:
                    raise ValueError('New publication bytes changed')
                for info in (os.fstat(fd), os.stat(leaf, dir_fd=directory, follow_symlinks=False)):
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or (info.st_uid, info.st_gid) != (0, 0) or stat.S_IMODE(info.st_mode) != 0o600
                            or A._identity(info) != A._identity(original)):
                        raise ValueError('Original private publication file required')

    def guard():
        package['check']()
        check_created()

    guard()
    space = os.fstatvfs(parent_fd)
    if space.f_bavail * space.f_frsize < sum(map(len, payload.values())) + 1024 * 1024:
        raise ValueError('Insufficient private publication space')
    attempt = '.candidate-attempt-' + secrets.token_hex(16)
    guard()
    try:
        os.mkdir(attempt, 0o700, dir_fd=parent_fd)
        parent_entries.add(attempt)

        def new_directory(ancestor, leaf):
            before = os.stat(leaf, dir_fd=ancestor, follow_symlinks=False)
            fd = os.open(leaf, flags, dir_fd=ancestor)
            stack.callback(os.close, fd)
            identity = _directory(os.fstat(fd), True)
            if identity != _directory(before, True):
                raise ValueError('Original new publication directory required')
            _entries(fd, set())
            chain.append((ancestor, leaf, fd, True, identity))
            return fd

        root_index = len(chain)
        directories[''] = new_directory(parent_fd, attempt)
        expected[''] = set()
        groups = set()
        for relative in payload:
            parts = relative.split('/')
            groups.update('/'.join(parts[:index]) for index in range(1, len(parts)))
        groups = sorted(groups, key=lambda name: (name.count('/'), name))
        for group in groups:
            ancestor, _, leaf = group.rpartition('/')
            guard()
            os.mkdir(leaf, 0o700, dir_fd=directories[ancestor])
            directories[group] = new_directory(directories[ancestor], leaf)
            expected[group] = set()
            expected[ancestor].add(leaf)
            guard()

        def copied(relative):
            guard()
            group, _, leaf = relative.rpartition('/')
            directory = directories[group]
            fd, info, raw = _write_private(stack, directory, leaf, payload[relative])
            files[relative] = directory, leaf, fd, info, raw
            expected[group].add(leaf)
            guard()

        for relative in sorted(key for key in payload if key != A.MANIFEST):
            copied(relative)
        for group in reversed(groups):
            os.fsync(directories[group])
        copied(A.MANIFEST)
        os.fsync(directories[''])
        os.fsync(parent_fd)
        guard()
        rename(parent_fd, attempt)
        # Retain the known created inode across the successful rename before any fallible operation.
        original = chain[root_index]
        chain[root_index] = original[0], published, *original[2:]
        parent_entries = {published}
        os.fsync(parent_fd)
        guard()
        result = verify(stack, parent / published)
        check_created()
        result['check']()
    except Exception as error:
        raise CandidatePublicationUncertain('Preserve publication parent; attempt or candidate may exist; inspect before retry') from error

    def check():
        check_created()
        result['check']()
        check_created()

    def read(relative):
        check()
        raw = result['read'](relative)
        check()
        return raw

    return {**result, 'check': check, 'read': read}


if __name__ == '__main__':
    print('Runtime candidate publication is internal-only; no changes performed', file=sys.stderr)
    sys.exit(1)
