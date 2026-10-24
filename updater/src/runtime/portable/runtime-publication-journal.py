# SPDX-License-Identifier: GPL-3.0-only
"""Private publication progress journal, never live ownership or recovery authority."""
from contextlib import ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import sys

PHASES = ('prepared', 'runtime-publish-intent', 'runtime-published',
    'config-publish-intent', 'config-published', 'registration-intent',
    'registered', 'verification-intent', 'verified')
LIMIT = 2048
FALSE_FLAGS = ('targetOwnershipVerified', 'installationAuthorized', 'recoveryAuthorized',
    'servicesRegistered', 'servicesActivated', 'installable', 'readyForActivation')


class JournalUncertain(RuntimeError):
    """A journal may exist or have changed; preserve it, never resume automatically."""


def _root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux private journal required')


def _path(value):
    if type(value) is not str:
        raise ValueError('Canonical private journal parent required')
    path = PurePosixPath(value)
    if (str(path) != value or len(path.parts) != 3 or path.parent != PurePosixPath('/root')
            or not re.fullmatch(r'droiddeck-publication-journal-[A-Za-z0-9_-]{1,80}', path.name)):
        raise ValueError('Only independently created private journal fixture parent allowed')
    return path


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _parse(records):
    if type(records) is not tuple or not 1 <= len(records) <= len(PHASES):
        raise ValueError('Bounded nonempty journal prefix required')
    previous, transaction, plan = '0' * 64, None, None
    for index, raw in enumerate(records):
        if type(raw) is not bytes or not 0 < len(raw) <= LIMIT:
            raise ValueError('Bounded canonical journal record required')
        row = json.loads(raw.decode('ascii'))
        if (type(row) is not dict or set(row) != {'version', 'kind', 'transaction', 'planSha256', 'step', 'phase', 'previousSha256'}
                or type(row['version']) is not int or row['version'] != 1
                or row['kind'] != 'private-publication-progress' or type(row['step']) is not int
                or row['step'] != index or row['phase'] != PHASES[index]
                or type(row['transaction']) is not str or not re.fullmatch('[a-f0-9]{32}', row['transaction'])
                or type(row['planSha256']) is not str or not re.fullmatch('[a-f0-9]{64}', row['planSha256'])
                or row['previousSha256'] != previous or _encode(row) != raw):
            raise ValueError('Closed canonical ordered journal prefix required')
        if index and (row['transaction'], row['planSha256']) != (transaction, plan):
            raise ValueError('One transaction and plan throughout journal required')
        transaction, plan, previous = row['transaction'], row['planSha256'], _sha(raw)
    # Hash chaining detects inconsistency, not authenticity or deletion of a whole suffix.
    return {'kind': 'private-publication-journal-observation', 'records': len(records),
        'lastPhase': PHASES[len(records) - 1], 'lastRecordSha256': previous,
        'review': 'manual-review-required', 'journalConsistencyVerified': True,
        'journalAuthenticityVerified': False, 'journalSuffixCompletenessVerified': False,
        'livePublicationPerformed': False, **dict.fromkeys(FALSE_FLAGS, False)}


def _dir(info, private=False):
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
            or info.st_mode & 0o022 or (private and stat.S_IMODE(info.st_mode) != 0o700)):
        raise ValueError('Original protected root-owned journal directory required')
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
            or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= LIMIT):
        raise ValueError('Bounded root600 single-link journal record required')
    return _identity(info)


def _read(fd):
    return os.pread(fd, LIMIT + 1, 0)


def _entries(fd, expected):
    seen = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in seen:
                raise ValueError('Uncertain journal inventory; preserve all entries')
            seen.add(entry.name)
    if seen != expected:
        raise ValueError('Incomplete journal inventory; preserve all entries')


def _hold_parent(stack, value):
    _root()
    path = _path(value)
    import resource
    required = len(os.listdir('/proc/self/fd')) + len(path.parts) + len(PHASES) + 5
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot retain entire journal')
    chain, parent = [], None
    for index, name in enumerate(path.parts):
        private = index == len(path.parts) - 1
        before = _dir(os.stat(name, dir_fd=parent, follow_symlinks=False), private)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if _dir(os.fstat(fd), private) != before:
            raise ValueError('Original journal ancestor required')
        chain.append((parent, name, fd, before, private))
        parent = fd

    def check():
        _root()
        for ancestor, name, fd, identity, private in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if _dir(info, private) != identity:
                    raise ValueError('Original journal ancestor namespace required')
    check()
    return chain, check, parent


def _rename_new(directory, source, target):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(directory, source.encode('ascii'), directory, target.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _held(parent_check, parent, directory, identity, records):
    def check(extra=()):
        parent_check()
        for info in (os.fstat(directory), os.stat('journal', dir_fd=parent, follow_symlinks=False)):
            if _dir(info, True) != identity:
                raise ValueError('Original journal namespace required')
        _entries(parent, {'journal'})
        _entries(directory, {row['name'] for row in records} | set(extra))
        for row in records:
            for after_read in (False, True):
                if after_read and _read(row['fd']) != row['raw']:
                    raise ValueError('Original committed journal bytes required')
                for info in (os.fstat(row['fd']), os.stat(row['name'], dir_fd=directory, follow_symlinks=False)):
                    if _regular(info) != row['identity']:
                        raise ValueError('Original committed journal inode required')
        parent_check()

    def observed():
        check()
        result = _parse(tuple(row['raw'] for row in records))
        check()
        return result

    return check, observed


def create_private_journal(stack, parent, plan_sha256):
    """New private fixture only. Caller progress assertions are not installation evidence."""
    if type(plan_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', plan_sha256):
        raise ValueError('Only a plan digest, never arbitrary payload, is accepted')
    _, parent_check, parent_fd = _hold_parent(stack, parent)
    _entries(parent_fd, set())
    records, transaction, failed = [], secrets.token_hex(16), False
    try:
        parent_check()
        os.mkdir('journal', 0o700, dir_fd=parent_fd)
        before = _dir(os.stat('journal', dir_fd=parent_fd, follow_symlinks=False), True)
        directory = os.open('journal', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        stack.callback(os.close, directory)
        if _dir(os.fstat(directory), True) != before:
            raise ValueError('Original exclusively created journal required')
        check, observed = _held(parent_check, parent_fd, directory, before, records)
        check()
        os.fsync(directory)
        os.fsync(parent_fd)

        def advance(phase):
            nonlocal failed
            if failed:
                raise JournalUncertain('Journal writer poisoned; preserve parent')
            index = len(records)
            if type(phase) is not str or index >= len(PHASES) or phase != PHASES[index]:
                raise ValueError('Only the next fixed publication phase is permitted')
            try:
                check()
                raw = _encode({'version': 1, 'kind': 'private-publication-progress', 'transaction': transaction,
                    'planSha256': plan_sha256, 'step': index, 'phase': phase,
                    'previousSha256': _sha(records[-1]['raw']) if records else '0' * 64})
                name, attempt = f'{index:04d}.json', '.record-' + secrets.token_hex(16)
                fd = os.open(attempt, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                stack.callback(os.close, fd)
                view = memoryview(raw)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise OSError('Incomplete journal write')
                    view = view[count:]
                identity = _regular(os.fstat(fd))
                if _read(fd) != raw or _regular(os.stat(attempt, dir_fd=directory, follow_symlinks=False)) != identity:
                    raise ValueError('Original newly written journal record required')
                os.fsync(fd)
                check((attempt,))
                # The only extra inventory entry allowed during this exclusive publication.
                _entries(directory, {row['name'] for row in records} | {attempt})
                if (_identity(os.fstat(fd)) != identity or _read(fd) != raw
                        or _regular(os.stat(attempt, dir_fd=directory, follow_symlinks=False)) != identity):
                    raise ValueError('Created journal bytes changed before publication')
                _rename_new(directory, attempt, name)
                after = _regular(os.fstat(fd))
                if after[:-1] != identity[:-1]:
                    raise ValueError('Only rename ctime change permitted on created journal record')
                records.append({'name': name, 'fd': fd, 'raw': raw, 'identity': after})
                os.fsync(directory)
                check()
                return observed()
            except Exception as error:
                failed = True
                raise JournalUncertain('Journal publication uncertain; preserve parent, never auto-resume') from error

        advance('prepared')
    except Exception as error:
        raise JournalUncertain('New journal may exist; preserve parent, never retry blindly') from error

    def retained():
        nonlocal failed
        if failed:
            raise JournalUncertain('Journal writer poisoned; preserve parent')
        try:
            return observed()
        except Exception as error:
            failed = True
            raise JournalUncertain('Journal observation uncertain; preserve parent') from error

    return {'advance': advance, 'observe': retained}


def inspect_private_journal(stack, parent):
    _, parent_check, parent_fd = _hold_parent(stack, parent)
    _entries(parent_fd, {'journal'})
    before = _dir(os.stat('journal', dir_fd=parent_fd, follow_symlinks=False), True)
    directory = os.open('journal', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    stack.callback(os.close, directory)
    if _dir(os.fstat(directory), True) != before:
        raise ValueError('Original journal directory required')
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in {f'{i:04d}.json' for i in range(len(PHASES))} or entry.name in names:
                raise ValueError('Incomplete or unknown journal entry; preserve all state')
            names.add(entry.name)
    if not names or names != {f'{i:04d}.json' for i in range(len(names))}:
        raise ValueError('Only a complete ordered journal prefix can be observed')
    records = []
    for name in sorted(names):
        initial = _regular(os.stat(name, dir_fd=directory, follow_symlinks=False))
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        if _regular(os.fstat(fd)) != initial:
            raise ValueError('Original journal record required')
        records.append({'name': name, 'fd': fd, 'raw': _read(fd), 'identity': initial})
    check, observed = _held(parent_check, parent_fd, directory, before, records)
    observed()
    return {'check': lambda: check(), 'observe': observed}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != '--inspect-private-journal':
        raise ValueError('Only read-only private journal inspection is exposed')
    with ExitStack() as stack:
        print(json.dumps(inspect_private_journal(stack, argv[1])['observe'](), sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Journal inspection refused; preserve state; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
