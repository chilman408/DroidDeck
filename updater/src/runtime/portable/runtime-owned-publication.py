# SPDX-License-Identifier: GPL-3.0-only
"""Current-session two-role publication under new private proof parents ONLY."""
import importlib.util
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys

spec = importlib.util.spec_from_file_location('owned_publication_journal', Path(__file__).with_name('runtime-publication-journal.py'))
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)
ROLES = ('runtime', 'config')


class PublicationUncertain(RuntimeError):
    """Created private trees or journal may exist; preserve, never adopt or retry."""


def _parent_path(value):
    if type(value) is not str:
        raise ValueError('Canonical new private publication parent required')
    path = PurePosixPath(value)
    if (str(path) != value or len(path.parts) != 3 or path.parent != PurePosixPath('/root')
            or not re.fullmatch(r'droiddeck-owned-publication-[A-Za-z0-9_-]{1,80}', path.name)):
        raise ValueError('Only new private owned-publication proof parent permitted')
    return path


def _hold_parent(stack, path):
    import resource
    # Journal admission later budgets its full lifetime on top of actual held payload FDs.
    required = len(os.listdir('/proc/self/fd')) + len(path.parts) + 3 + 4
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot hold publication ownership')
    chain, parent = [], None
    for index, part in enumerate(path.parts):
        private = index == len(path.parts) - 1
        before = J._dir(os.stat(part, dir_fd=parent, follow_symlinks=False), private)
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if J._dir(os.fstat(fd), private) != before:
            raise ValueError('Original publication parent required')
        chain.append((parent, part, fd, before, private))
        parent = fd

    def check():
        J._root()
        for ancestor, part, fd, identity, private in chain:
            for info in (os.fstat(fd), os.stat(part, dir_fd=ancestor, follow_symlinks=False)):
                if J._dir(info, private) != identity:
                    raise ValueError('Original publication parent namespace required')
    check()
    return parent, check


def _nonempty(fd):
    with os.scandir(fd) as entries:
        if next(entries, None) is None:
            raise ValueError('Empty role must not be published')


def _write_record(stack, parent, raw):
    fd = os.open('allocation.json', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    stack.callback(os.close, fd)
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Incomplete owned publication allocation record')
        view = view[count:]
    identity = J._regular(os.fstat(fd))
    if J._read(fd) != raw:
        raise ValueError('Created allocation record bytes required')
    os.fsync(fd)
    return fd, identity


def prepare_private_publication(stack, parent, journal_parent, plan_sha256, populate):
    """Trusted populate(role, newly-created-fd) returns payload check/sync closures.

    This mechanism does NOT admit artifact provenance or validate payload semantics.
    The trusted caller must supply those separately before future live integration.
    """
    J._root()
    path = _parent_path(parent)
    J._path(journal_parent)
    if type(plan_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', plan_sha256) or not callable(populate):
        raise ValueError('Plan digest and trusted in-process payload builder required')
    parent_fd, parent_check = _hold_parent(stack, path)
    _, journal_parent_check, journal_parent_fd = J._hold_parent(stack, journal_parent)
    J._entries(parent_fd, set())
    J._entries(journal_parent_fd, set())
    created, record, journal, completed, poisoned = {}, None, None, 0, False
    transaction = secrets.token_hex(16)

    def check():
        parent_check()
        journal_parent_check()
        if journal is None:
            J._entries(journal_parent_fd, set())
        expected = {item['name'] for item in created.values()}
        if record is not None:
            expected.add('allocation.json')
        J._entries(parent_fd, expected)
        for item in created.values():
            for info in (os.fstat(item['fd']), os.stat(item['name'], dir_fd=parent_fd, follow_symlinks=False)):
                if J._dir(info, True) != item['identity']:
                    raise ValueError('Original exclusively created role directory required')
            if item['payload'] is not None:
                item['payload']['check']()
                _nonempty(item['fd'])
        if record is not None:
            fd, identity, raw = record
            for after_read in (False, True):
                if after_read and J._read(fd) != raw:
                    raise ValueError('Original allocation record bytes required')
                for info in (os.fstat(fd), os.stat('allocation.json', dir_fd=parent_fd, follow_symlinks=False)):
                    if J._regular(info) != identity:
                        raise ValueError('Original single-link allocation record required')
        parent_check()

    try:
        for role in ROLES:
            check()
            name = '.' + role + '-attempt-' + transaction
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            initial = J._dir(os.stat(name, dir_fd=parent_fd, follow_symlinks=False), True)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            stack.callback(os.close, fd)
            if J._dir(os.fstat(fd), True) != initial or initial[0] != os.fstat(parent_fd).st_dev:
                raise ValueError('Original newly created same-filesystem role required')
            J._entries(fd, set())
            created[role] = {'name': name, 'fd': fd, 'identity': initial, 'payload': None}
            check()
        raw = J._encode({'version': 1, 'kind': 'private-created-publication-allocation',
            'transaction': transaction, 'planSha256': plan_sha256,
            'parent': {'device': os.fstat(parent_fd).st_dev, 'inode': os.fstat(parent_fd).st_ino},
            'roles': [{'role': role, 'createdName': item['name'], 'device': item['identity'][0],
                'inode': item['identity'][1]} for role, item in created.items()],
            'scope': 'private-current-session-only', 'recoveryAuthorized': False})
        fd, identity = _write_record(stack, parent_fd, raw)
        record = fd, identity, raw
        check()
        for role, item in created.items():
            payload = populate(role, item['fd'])
            if type(payload) is not dict or set(payload) != {'check', 'sync'} or not all(callable(v) for v in payload.values()):
                raise ValueError('Trusted retained payload check and sync closures required')
            item['payload'] = payload
            check()
            payload['sync']()
            os.fsync(item['fd'])
            check()
        os.fsync(parent_fd)
        check()
        # The separate journal has its own exact parent inventory and retained originals.
        journal = J.create_private_journal(stack, journal_parent, J._sha(raw))
        check()
        journal['observe']()
    except Exception as error:
        raise PublicationUncertain('Private allocation or journal may exist; preserve both parents') from error

    def observe():
        nonlocal poisoned
        if poisoned:
            raise PublicationUncertain('Publication poisoned; preserve both parents')
        try:
            check()
            progress = journal['observe']()
            expected = ('prepared', 'runtime-published', 'config-published')[completed]
            if progress['lastPhase'] != expected:
                raise ValueError('Original journal phase must match current-session namespace')
            check()
            return {**progress, 'kind': 'private-owned-publication', 'privateRolesPublished': completed,
                'createdRoleIdentitiesHeld': True, 'privateTargetOwnershipVerified': completed > 0,
                'ownershipScope': 'private-current-session-only', 'allocationSha256': J._sha(record[2]),
                'payloadAdmissionVerified': False, 'livePublicationPerformed': False,
                'registrationAttempted': False, 'restartRecoveryVerified': False}
        except Exception as error:
            poisoned = True
            raise PublicationUncertain('Publication observation uncertain; preserve both parents') from error

    def publish_next():
        nonlocal completed, poisoned
        if poisoned:
            raise PublicationUncertain('Publication poisoned; preserve both parents')
        if completed == len(ROLES):
            raise ValueError('Both fixed private roles already published')
        try:
            observe()
            role = ROLES[completed]
            item = created[role]
            journal['advance'](role + '-publish-intent')
            check()
            item['payload']['sync']()
            os.fsync(item['fd'])
            check()
            if journal['observe']()['lastPhase'] != role + '-publish-intent':
                raise ValueError('Original durable intent required immediately before role rename')
            J._rename_new(parent_fd, item['name'], role)
            # Rebind retained created inode before any fallible operation, not after fsync.
            item['name'] = role
            os.fsync(parent_fd)
            check()
            journal['advance'](role + '-published')
            completed += 1
            return observe()
        except Exception as error:
            poisoned = True
            raise PublicationUncertain('Partial private publication possible; preserve both parents, never retry') from error

    observe()
    return {'observe': observe, 'publish_next': publish_next}


def main(argv=None):
    raise ValueError('No install, publication, adoption or recovery CLI is exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No publication CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
