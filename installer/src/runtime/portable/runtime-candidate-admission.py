# SPDX-License-Identifier: GPL-3.0-only
"""Read-only privileged admission for the fixed reviewed runtime source candidate."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import sys
from types import MappingProxyType

MANIFEST = 'bundle-manifest.json'
MANIFEST_SHA256 = '6d136e676dccc2a2b520fe8731d52d700e957a1b99f6a9de033915ad275f89c8'
FALSE_FLAGS = ('rootPrivateCopyVerified', 'sourceFilesInstalled', 'installable',
               'readyForActivation', 'servicesActivated', 'androidOperationsExecuted')


def candidate_records(raw):
    if (type(raw) is not bytes or not 0 < len(raw) <= 65536
            or hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256):
        raise ValueError('Independently pinned runtime candidate manifest required')
    value = json.loads(raw)
    rows = value['files']
    if value['kind'] != 'runtime-source-candidate' or len(rows) != 76:
        raise ValueError('Closed runtime candidate recipe required')
    result = {}
    for row in rows:
        name, size, digest = row['path'], row['size'], row['sha256']
        if (set(row) != {'path', 'size', 'sha256'} or type(name) is not str
                or not re.fullmatch('(portable|preview-gateway|waydroid-display|browser-connection)/[a-z0-9][a-z0-9.-]*', name)
                or name in result or type(size) is not int or not 0 < size <= 1024 * 1024
                or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Closed runtime candidate payload required')
        result[name] = size, digest
    return MappingProxyType(result)


def _root_linux():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Privileged Linux candidate admission required')


def _canonical(root):
    value = str(root)
    path = PurePosixPath(value)
    if ('\x00' in value or not 0 < len(value) <= 4096 or not path.is_absolute() or value == '/'
            or value.startswith('//') or str(path) != value or '..' in path.parts):
        raise ValueError('Canonical nonroot candidate path required')
    return path


def _owner(value):
    if (type(value) is not tuple or len(value) != 2
            or any(type(item) is not int or not 0 <= item < 2**31 for item in value)
            or (value != (0, 0) and 0 in value)):
        raise ValueError('Independent candidate owner required')
    return value


def policy(owner):
    return {'kind': 'held-runtime-source-candidate', 'sourceCandidateVerified': True,
            'payloadSourcesVerified': 76, 'sourceOwnerMatched': True,
            'rootPrivateSourceCandidate': owner == (0, 0), **dict.fromkeys(FALSE_FLAGS, False)}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(fd, limit):
    blocks, offset = [], 0
    while offset <= limit:
        block = os.pread(fd, min(1024 * 1024, limit + 1 - offset), offset)
        if not block:
            break
        blocks.append(block)
        offset += len(block)
    return b''.join(blocks)


def hold_runtime_candidate(stack, root, *, owner=(0, 0)):
    """Keep original namespace and files open; caller must retain stack and recheck."""
    held = _hold_pinned_tree(stack, root, owner=owner, records_reader=candidate_records, manifest_limit=65536)
    return {**held, 'policy': policy(owner)}


def _hold_pinned_tree(stack, root, *, owner, records_reader, manifest_limit):
    """Internal mechanism; production callers independently fix their recipe parser."""
    _root_linux()
    owner, path = _owner(owner), _canonical(root)
    chain, directories, records = [], {}, {}

    def directory(info, private):
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner[0]) or info.st_mode & 0o022
                or (private and ((info.st_uid, info.st_gid) != owner or stat.S_IMODE(info.st_mode) != 0o700))):
            raise ValueError('Protected original candidate directory required')

    def opened(name, parent=None, private=False):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        directory(info, private)
        identity = info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
        chain.append((parent, name, fd, private, identity))
        return fd

    current = opened('/')
    for index, name in enumerate(path.parts[1:]):
        current = opened(name, current, private=index == len(path.parts) - 2)
    directories[''] = current

    def file_check(parent, name, fd, original):
        for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_uid, info.st_gid) != owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or _identity(info) != _identity(original)):
                raise ValueError('Original single-link private candidate file required')

    def file_open(parent, name, limit, expected=None):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        file_check(parent, name, fd, info)
        if not (0 if expected is not None else 1) <= info.st_size <= limit or (expected and info.st_size != expected[0]):
            raise ValueError('Bounded candidate file required')
        raw = _read(fd, limit)
        pin = len(raw), hashlib.sha256(raw).hexdigest()
        if len(raw) != info.st_size or (expected and pin != expected):
            raise ValueError('Candidate differs from independent recipe')
        file_check(parent, name, fd, info)
        return parent, name, fd, info, raw, pin

    records[MANIFEST] = file_open(current, MANIFEST, manifest_limit)
    pins = records_reader(records[MANIFEST][4])
    inventory = {'': {MANIFEST}}
    for relative in pins:
        parts = relative.split('/')
        for index, name in enumerate(parts):
            inventory.setdefault('/'.join(parts[:index]), set()).add(name)
    for relative in sorted((key for key in inventory if key), key=lambda key: (key.count('/'), key)):
        parent, _, name = relative.rpartition('/')
        directories[relative] = opened(name, directories[parent], private=True)

    def check():
        _root_linux()
        for parent, name, fd, private, before in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                directory(info, private)
                if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) != before:
                    raise ValueError('Original candidate namespace generation required')
        for relative, fd in directories.items():
            names = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.name not in inventory[relative] or entry.name in names:
                        raise ValueError('Closed candidate inventory required')
                    names.add(entry.name)
            if names != inventory[relative]:
                raise ValueError('Closed candidate inventory required')
        for parent, name, fd, info, raw, pin in records.values():
            file_check(parent, name, fd, info)
            actual = _read(fd, len(raw))
            if (len(actual), hashlib.sha256(actual).hexdigest()) != pin:
                raise ValueError('Held candidate bytes changed')
            file_check(parent, name, fd, info)

    check()
    for relative, pin in sorted(pins.items()):
        parent, _, name = relative.rpartition('/')
        records[relative] = file_open(directories[parent], name, pin[0], pin)
    check()

    def read(relative):
        if type(relative) is not str or relative not in records:
            raise ValueError('Closed candidate read required')
        check()
        raw = records[relative][4]
        check()
        return raw

    return {'check': check, 'read': read, 'files': tuple(sorted(records))}


def _account(user):
    import pwd
    try:
        account = pwd.getpwnam(user)
    except KeyError as error:
        raise ValueError('Existing source account required') from error
    owner = _owner((account.pw_uid, account.pw_gid))
    if (account.pw_name != user or owner == (0, 0)
            or not re.fullmatch('/home/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', account.pw_dir)
            or account.pw_dir.endswith('.')):
        raise ValueError('Unprivileged source account and canonical home required')
    return user, *owner, account.pw_dir


def hold_user_candidate(stack, user, root):
    _root_linux()
    if type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
        raise ValueError('Exact source account name required')
    source = _canonical(root)
    account = _account(user)
    home = PurePosixPath(account[3])
    if source == home or not source.is_relative_to(home):
        raise ValueError('Candidate below independently resolved account home required')
    held = hold_runtime_candidate(stack, source, owner=account[1:3])

    def check():
        if _account(user) != account:
            raise ValueError('Source account changed during candidate admission')
        held['check']()
        if _account(user) != account:
            raise ValueError('Source account changed during candidate admission')

    def read(relative):
        check()
        raw = held['read'](relative)
        check()
        return raw

    check()
    return {**held, 'check': check, 'read': read, 'policy': {**held['policy'], 'sourceAccountMatched': True}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as stack:
        if len(argv) == 2 and argv[0] == '--verify-package':
            held = hold_runtime_candidate(stack, argv[1])
        elif len(argv) == 3 and argv[0] == '--verify-user':
            held = hold_user_candidate(stack, argv[1], argv[2])
        else:
            raise ValueError('Only read-only fixed candidate verification is available')
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Runtime candidate admission refused; no changes performed', file=sys.stderr)
        sys.exit(1)
