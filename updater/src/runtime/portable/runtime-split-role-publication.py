# SPDX-License-Identifier: GPL-3.0-only
"""Private two-device publication experiment; never live installation or recovery."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys

spec = importlib.util.spec_from_file_location('split_owned_publication', Path(__file__).with_name('runtime-owned-publication.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
J = P.J
ROLES = P.ROLES
Uncertain = P.PublicationUncertain


def _path(value, role):
    if type(value) is not str or role not in ROLES:
        raise ValueError('Fixed private role parent required')
    path = PurePosixPath(value)
    base = '/root' if role == 'runtime' else '/run'
    if (str(path) != value or len(path.parts) != 3 or path.parent != PurePosixPath(base)
            or not re.fullmatch('droiddeck-split-' + role + '-[A-Za-z0-9_-]{1,80}', path.name)):
        raise ValueError('Only fresh private root/run split-role proof parents permitted')
    return path


def _different_devices(devices):
    if devices[0] == devices[1]:
        raise ValueError('Two already-mounted distinct devices required; no mounting or fallback')


def _serialize_parents(stack, descriptors):
    """Advisory locks on original private directory inodes, never pathname lock files."""
    import fcntl
    J._root()
    if type(descriptors) is not tuple or len(descriptors) != 3 or any(type(fd) is not int for fd in descriptors):
        raise ValueError('Three retained private parent descriptors required')
    ordered = [(J._dir(os.fstat(fd), True)[:2], fd) for fd in descriptors]
    if len({identity for identity, _ in ordered}) != 3:
        raise ValueError('Three distinct private parent identities required')
    with ExitStack() as acquired:
        for _, fd in sorted(ordered):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Private publication parents busy; no allocation performed') from error
            acquired.callback(fcntl.flock, fd, fcntl.LOCK_UN)
        # Transfer only the complete lock set; a failed acquisition unwinds immediately.
        stack.enter_context(acquired.pop_all())


def _storage_preflight(descriptors, plan=None, *, private_parents=True):
    """Observe capacity on retained parents; never reserve blocks or inodes."""
    if type(private_parents) is not bool or type(descriptors) is not dict or set(descriptors) != {'runtime', 'config', 'journal'}:
        raise ValueError('Three fixed storage parent descriptors required')
    supplied = plan is not None
    if plan is None:
        plan = {role: {'sizes': (), 'directories': 1, 'links': 0} for role in ROLES}
    if type(plan) is not dict or set(plan) != set(ROLES):
        raise ValueError('Closed two-role storage plan required')
    rows, total = {}, 0
    for role in ROLES:
        row = plan[role]
        if (type(row) is not dict or set(row) != {'sizes', 'directories', 'links'}
                or type(row['sizes']) is not tuple or not 0 <= len(row['sizes']) <= 1024
                or any(type(size) is not int or not 0 < size <= 8 * 1024 * 1024 for size in row['sizes'])
                or type(row['directories']) is not int or not 1 <= row['directories'] <= 256
                or type(row['links']) is not int or not 0 <= row['links'] <= 64):
            raise ValueError('Bounded role storage counts required')
        total += sum(row['sizes'])
        rows[role] = (row['sizes'], row['directories'], row['links'])
    if total > 512 * 1024 * 1024:
        raise ValueError('Bounded combined storage payload required')
    rows['runtime'] = (rows['runtime'][0] + (J.LIMIT,), *rows['runtime'][1:])
    rows['journal'] = ((J.LIMIT,) * len(J.PHASES), 1, 0)
    groups = {}
    for role, (sizes, directories, links) in rows.items():
        fd = descriptors[role]
        if type(fd) is not int or fd < 0:
            raise ValueError('Retained storage descriptor required')
        info, space = os.fstat(fd), os.fstatvfs(fd)
        J._dir(info, private_parents)
        fields = (space.f_frsize, space.f_blocks, space.f_bavail, space.f_files, space.f_favail, space.f_flag)
        if (any(type(value) is not int or value < 0 for value in fields)
                or not 0 < space.f_frsize <= 1024 * 1024 or not space.f_blocks or not space.f_files
                or space.f_bavail > space.f_blocks or space.f_favail > space.f_files
                or space.f_flag & os.ST_RDONLY):
            raise ValueError('Writable bounded filesystem capacity required')
        group = groups.setdefault(info.st_dev, {'fragment': space.f_frsize, 'bytes': 0, 'inodes': 0,
            'available': space.f_bavail * space.f_frsize, 'freeInodes': space.f_favail})
        if group['fragment'] != space.f_frsize:
            raise ValueError('Consistent shared filesystem fragment size required')
        group['available'] = min(group['available'], space.f_bavail * space.f_frsize)
        group['freeInodes'] = min(group['freeInodes'], space.f_favail)
        # Each file rounds independently; allow one block per directory and alias too.
        group['bytes'] += sum(((size + space.f_frsize - 1) // space.f_frsize) * space.f_frsize for size in sizes)
        group['bytes'] += (directories + links) * space.f_frsize
        group['inodes'] += len(sizes) + directories + links
    if any(group['available'] < group['bytes'] + 1024 * 1024
           or group['freeInodes'] < group['inodes'] + 16 for group in groups.values()):
        raise ValueError('Insufficient combined publication blocks or inodes')
    return {'privateParentSpacePreflightVerified': True, 'privatePayloadSpacePlanSupplied': supplied,
        'storageFilesystemsObserved': len(groups), 'storageCapacityReserved': False,
        'storageSuccessGuaranteed': False}


def prepare_split_publication(stack, runtime_parent, config_parent, journal_parent, plan_sha256, populate, *, storage_plan=None):
    """Trusted builder receives only newly created role FDs and supplies check/sync.

    /run is an EPHEMERAL TEST FIXTURE, not a persistent configuration destination.
    Independent per-parent moves are NOT one atomic transaction across devices.
    Builder provenance, payload admission and its descriptor budget remain caller duties.
    """
    J._root()
    paths = {role: _path(value, role) for role, value in zip(ROLES, (runtime_parent, config_parent))}
    J._path(journal_parent)
    if type(plan_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', plan_sha256) or not callable(populate):
        raise ValueError('Plan digest and trusted in-process builder required')
    import resource
    required = len(os.listdir('/proc/self/fd')) + 9 + 2 + 1 + 3 + 1 + len(J.PHASES) + 8
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot retain split publication')
    parents = {role: P._hold_parent(stack, path) for role, path in paths.items()}
    _, journal_check, journal_fd = J._hold_parent(stack, journal_parent)
    _serialize_parents(stack, (*[fd for fd, _ in parents.values()], journal_fd))
    for _, guard in parents.values():
        guard()
    journal_check()
    devices = tuple(os.fstat(parents[role][0]).st_dev for role in ROLES)
    _different_devices(devices)
    for fd, _ in parents.values():
        J._entries(fd, set())
    J._entries(journal_fd, set())
    storage = _storage_preflight({**{role: fd for role, (fd, _) in parents.items()}, 'journal': journal_fd}, storage_plan)
    created, record, journal, completed, poisoned = {}, None, None, 0, False
    transaction = secrets.token_hex(16)

    def check():
        for _, guard in parents.values():
            guard()
        journal_check()
        if journal is None:
            J._entries(journal_fd, set())
        for role, (parent, _) in parents.items():
            item = created.get(role)
            expected = {item['name']} if item is not None else set()
            if role == 'runtime' and record is not None:
                expected.add('allocation.json')
            J._entries(parent, expected)
            if item is not None:
                for info in (os.fstat(item['fd']), os.stat(item['name'], dir_fd=parent, follow_symlinks=False)):
                    if J._dir(info, True) != item['identity']:
                        raise ValueError('Original exclusively created split-role inode required')
                if item['payload'] is not None:
                    item['payload']['check']()
                    P._nonempty(item['fd'])
        if record is not None:
            fd, identity, raw = record
            for after_read in (False, True):
                if after_read and J._read(fd) != raw:
                    raise ValueError('Original split allocation bytes required')
                for info in (os.fstat(fd), os.stat('allocation.json', dir_fd=parents['runtime'][0], follow_symlinks=False)):
                    if J._regular(info) != identity:
                        raise ValueError('Original single-link split allocation required')
        for _, guard in parents.values():
            guard()
        journal_check()

    try:
        for role, (parent, _) in parents.items():
            check()
            name = '.' + role + '-attempt-' + transaction
            os.mkdir(name, 0o700, dir_fd=parent)
            before = J._dir(os.stat(name, dir_fd=parent, follow_symlinks=False), True)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            if J._dir(os.fstat(fd), True) != before or before[0] != os.fstat(parent).st_dev:
                raise ValueError('New role must be on its own retained parent filesystem')
            J._entries(fd, set())
            created[role] = {'name': name, 'fd': fd, 'identity': before, 'payload': None}
            check()
        raw = J._encode({'version': 1, 'kind': 'private-split-publication-allocation',
            'transaction': transaction, 'planSha256': plan_sha256,
            'parents': [{'role': role, 'device': os.fstat(fd).st_dev, 'inode': os.fstat(fd).st_ino}
                for role, (fd, _) in parents.items()],
            'roles': [{'role': role, 'createdName': item['name'], 'device': item['identity'][0],
                'inode': item['identity'][1]} for role, item in created.items()],
            'scope': 'private-current-session-only', 'recoveryAuthorized': False})
        fd, identity = P._write_record(stack, parents['runtime'][0], raw)
        record = fd, identity, raw
        check()
        for role, item in created.items():
            payload = populate(role, item['fd'])
            if type(payload) is not dict or set(payload) != {'check', 'sync'} or not all(callable(v) for v in payload.values()):
                raise ValueError('Trusted retained payload check/sync closures required')
            item['payload'] = payload
            check()
            payload['sync']()
            os.fsync(item['fd'])
            check()
        for fd, _ in parents.values():
            os.fsync(fd)
        check()
        journal = J.create_private_journal(stack, journal_parent, J._sha(raw))
        check()
        journal['observe']()
    except Exception as error:
        raise Uncertain('Partial split allocation possible; preserve ALL THREE parents') from error

    def observe():
        nonlocal poisoned
        if poisoned:
            raise Uncertain('Split context poisoned; preserve all three parents')
        try:
            check()
            progress = journal['observe']()
            if progress['lastPhase'] != ('prepared', 'runtime-published', 'config-published')[completed]:
                raise ValueError('Original journal must match current-session split namespace')
            check()
            return {**progress, **storage, 'kind': 'private-split-role-publication', 'privateRolesPublished': completed,
                'createdRoleIdentitiesHeld': True, 'privateTargetOwnershipVerified': completed > 0,
                'privateDistinctDevicesVerified': True, 'privateCrossDeviceRolePublicationVerified': completed == 2,
                'privateParentSerializationHeld': True,
                'parentSerializationScope': 'advisory-current-context-only',
                'ownershipScope': 'private-current-session-only', 'allocationSha256': J._sha(record[2]),
                'crossFilesystemPublicationVerified': False, 'globallyAtomic': False,
                'persistentConfigDestinationVerified': False, 'payloadAdmissionVerified': False,
                'livePublicationPerformed': False, 'registrationAttempted': False,
                'restartRecoveryVerified': False, 'powerLossRecoveryVerified': False}
        except Exception as error:
            poisoned = True
            raise Uncertain('Split observation uncertain; preserve all three parents') from error

    def publish_next():
        nonlocal completed, poisoned
        if poisoned:
            raise Uncertain('Split context poisoned; preserve all three parents')
        if completed == len(ROLES):
            raise ValueError('Both fixed private roles already published')
        try:
            observe()
            role = ROLES[completed]
            item, parent = created[role], parents[role][0]
            journal['advance'](role + '-publish-intent')
            check()
            item['payload']['sync']()
            os.fsync(item['fd'])
            if journal['observe']()['lastPhase'] != role + '-publish-intent':
                raise ValueError('Durable intent required before split-role move')
            check()
            J._rename_new(parent, item['name'], role)
            # Keep the created inode bound even when the following parent fsync fails.
            item['name'] = role
            os.fsync(parent)
            check()
            journal['advance'](role + '-published')
            completed += 1
            return observe()
        except Exception as error:
            poisoned = True
            raise Uncertain('Partial split publication possible; preserve all parents, never retry') from error

    observe()
    return {'observe': observe, 'publish_next': publish_next}


def main(argv=None):
    raise ValueError('No split publication, install, adoption or recovery CLI exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No split publication CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
