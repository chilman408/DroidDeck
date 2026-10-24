# SPDX-License-Identifier: GPL-3.0-only
"""Allocate two NEW fixed empty targets; no payload, registration or recovery CLI."""
import os
from pathlib import PurePosixPath
import re
import stat
import sys
from types import MappingProxyType

PARENTS = (PurePosixPath('/opt'), PurePosixPath('/etc'))
ROLES = ('runtime', 'config')
NAME = 'droiddeck'


class AllocationUncertain(RuntimeError):
    """A target may exist: preserve both namespaces; never adopt, remove or retry."""


def _root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux new-target allocation required')


def _directory(info, private=False):
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
            or info.st_mode & 0o022 or (private and stat.S_IMODE(info.st_mode) != 0o700)):
        raise ValueError('Protected original root-owned target directory required')
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _parents(values):
    if type(values) is not tuple or len(values) != 2 or any(type(value) is not str for value in values):
        raise ValueError('Two fixed canonical target parents required')
    paths = tuple(PurePosixPath(value) for value in values)
    if any(str(path) != value or not path.is_absolute() for path, value in zip(paths, values)):
        raise ValueError('Canonical target parents required')
    if paths == PARENTS:
        return paths
    # Internal isolated fixture seam, never an arbitrary live-path override or CLI option.
    base = paths[0].parent
    if (paths != (base / 'opt', base / 'etc') or base.parent != PurePosixPath('/root')
            or not re.fullmatch('droiddeck-target-allocation-[A-Za-z0-9_-]{1,80}', base.name)):
        raise ValueError('Only fixed live parents or new root-private fixture parents required')
    return paths


def _hold_parent(stack, path):
    chain, parent = [], None
    for part in path.parts:
        before = _directory(os.stat(part, dir_fd=parent, follow_symlinks=False))
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if _directory(os.fstat(fd)) != before:
            raise ValueError('Original protected target ancestor required')
        chain.append((parent, part, fd, before))
        parent = fd

    def check():
        _root()
        for ancestor, part, fd, identity in chain:
            for info in (os.fstat(fd), os.stat(part, dir_fd=ancestor, follow_symlinks=False)):
                if _directory(info) != identity:
                    raise ValueError('Original protected target namespace required')
    check()
    return parent, check


def _absent(parent):
    try:
        os.stat(NAME, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('Existing installation targets must never be adopted')


def _allocate_targets(stack, parents, layout_sha256, check_sources, *, before_create=None):
    _root()
    paths = _parents(parents)
    if type(layout_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', layout_sha256) or not callable(check_sources):
        raise ValueError('Fresh admitted layout digest and retained source checker required')
    if before_create is not None and not callable(before_create):
        raise ValueError('Trusted preallocation checker required')
    import resource
    required = len(os.listdir('/proc/self/fd')) + sum(len(path.parts) for path in paths) + 2 + 4
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot retain installation targets')
    held = {role: _hold_parent(stack, path) for role, path in zip(ROLES, paths)}
    if len({(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd, _ in held.values()}) != 2:
        raise ValueError('Distinct original target parents required')
    created, poisoned, active = {}, False, True

    def ended():
        nonlocal active
        active = False
    stack.callback(ended)

    def check():
        _root()
        if not active:
            raise ValueError('Original allocation context already closed')
        check_sources()
        for role, (parent, guard) in held.items():
            guard()
            if role not in created:
                _absent(parent)
            else:
                fd, identity = created[role]
                for info in (os.fstat(fd), os.stat(NAME, dir_fd=parent, follow_symlinks=False)):
                    if _directory(info, True) != identity:
                        raise ValueError('Original exclusively allocated target inode required')
            guard()
        check_sources()

    # Entire new namespace admission before the first mutation.
    check()
    if before_create is not None:
        before_create(MappingProxyType({role: fd for role, (fd, _) in held.items()}))
        check()
    try:
        for role, (parent, _) in held.items():
            check()
            os.mkdir(NAME, 0o700, dir_fd=parent)
            before = _directory(os.stat(NAME, dir_fd=parent, follow_symlinks=False), True)
            fd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            if _directory(os.fstat(fd), True) != before or before[0] != os.fstat(parent).st_dev:
                raise ValueError('New directory on original parent filesystem required')
            if os.listdir(fd):
                raise ValueError('Newly allocated target must start empty')
            created[role] = fd, before
            os.fsync(fd)
            os.fsync(parent)
            check()
    except Exception as error:
        raise AllocationUncertain('Partial target allocation possible; preserve both targets, no retry or cleanup') from error

    def observe():
        nonlocal poisoned
        if poisoned:
            raise AllocationUncertain('Target allocation context poisoned; preserve both targets')
        try:
            check()
            return {'kind': 'held-new-fixed-install-targets', 'layoutSha256': layout_sha256,
                'newTargetDirectoriesCreated': 2, 'targetCreatedInodesHeld': True,
                'targetOwnershipScope': 'current-context-new-directory-inodes-only',
                'targetDirectoryOwnershipVerified': True,
                **dict.fromkeys(('payloadVerified', 'filesWritten', 'finalPermissionsApplied',
                    'servicesRegistered', 'servicesActivated', 'installationAuthorized',
                    'recoveryAuthorized', 'restartRecoveryVerified', 'persistentStorageVerified',
                    'installable', 'readyForActivation'), False)}
        except Exception as error:
            poisoned = True
            raise AllocationUncertain('Target allocation changed; preserve both targets') from error

    observe()
    return {'observe': observe, 'check': lambda: observe(),
        'directory_fds': MappingProxyType({role: fd for role, (fd, _) in created.items()})}


def allocate_targets(stack, layout_sha256, check_sources):
    """Trusted future installer only; fixed /opt and /etc, no path overrides."""
    return _allocate_targets(stack, tuple(map(str, PARENTS)), layout_sha256, check_sources)


def main(argv=None):
    raise ValueError('No target allocation, install, adoption or recovery CLI exposed')


if __name__ == '__main__':
    print('No target allocation CLI; no installation or recovery performed', file=sys.stderr)
    sys.exit(1)
