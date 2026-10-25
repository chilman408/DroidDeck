# SPDX-License-Identifier: GPL-3.0-only
"""Durable owned-file finalization and registration, never service activation."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import sys


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


O = _load('runtime-owned-targets')
J = _load('runtime-publication-journal')
K, A = O.K, O.A
JOURNAL = '/var/lib/droiddeck/registration-transaction'
LIMIT = 1024 * 1024
UNITS = ('waydroid-container.service', 'tesla-waydroid-compositor.service',
    'tesla-waydroid-headless-session.service', 'tesla-waydroid-runtime.service',
    'droiddeck-browser-connection.service')


class RegistrationUncertain(RuntimeError):
    """Preserve owned targets, staged files and intent; inspect or explicitly resume."""


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return info.st_dev, info.st_ino


def _inactive():
    result = subprocess.run(['/usr/bin/systemctl', 'show', '--property=ActiveState', '--value', *UNITS],
        capture_output=True, check=True, timeout=10, cwd='/',
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'})
    if (result.stderr or not 0 < len(result.stdout) <= 4096
            or [line for line in result.stdout.splitlines() if line] != [b'inactive'] * len(UNITS)):
        raise ValueError('All Android and DroidDeck workers must remain inactive')


class _Directories:
    def __init__(self, stack):
        self.stack, self.nodes = stack, {}

    def open(self, path, *, create=False):
        path = PurePosixPath(path)
        if not path.is_absolute() or (str(path) != '/' and str(path) != str(A._canonical(str(path)))):
            raise ValueError('Canonical protected directory required')
        if path in self.nodes:
            self.check()
            return self.nodes[path][0]
        parent = None if str(path) == '/' else self.open(path.parent, create=create)
        name = '/' if parent is None else path.name
        made = False
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(name, 0o700, dir_fd=parent)
            made = True
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        self.stack.callback(os.close, fd)
        if made:
            os.fchmod(fd, 0o755)
            os.fsync(fd)
            os.fsync(parent)
        info = os.fstat(fd)
        O.C._directory(info)
        self.nodes[path] = (fd, parent, name, O.C._directory(info))
        self.check()
        return fd

    def check(self):
        A._root_linux()
        for fd, parent, name, expected in self.nodes.values():
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if O.C._directory(info) != expected:
                    raise ValueError('Protected registration ancestor changed')


def _absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('Existing registration or intent must not be adopted')


def _file(dirs, path, size, pin, mode):
    path = PurePosixPath(path)
    parent = dirs.open(path.parent)
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != mode or before.st_size != size):
            raise ValueError('Exact protected single-link registration input required')
        raw = A._read(fd, size)
        if len(raw) != size or _sha(raw) != pin:
            raise ValueError('Protected registration payload changed')
        for info in (os.fstat(fd), os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
            if A._identity(info) != A._identity(before):
                raise ValueError('Original protected payload generation changed')
        dirs.check()
        return before, raw
    finally:
        os.close(fd)


def _plan(authority, prefix):
    authority['check']()
    raw = authority['read_plan']()
    plan = json.loads(raw)
    if (plan.get('kind') != 'final-install-placement-plan' or _encode(plan) != raw
            or plan.get('registrationTargets') != sorted(K.REGISTRATIONS.values())):
        raise ValueError('Original canonical final placement authority required')
    prefix = Path(prefix)

    def mapped(name):
        return str(prefix / str(A._canonical(name)).lstrip('/'))

    files, registrations = {}, {}
    for row in plan['files']:
        name = row['path']
        if (type(row['registration']) is not bool or (row['uid'], row['gid']) != (0, 0)
                or type(row['size']) is not int or not 0 < row['size'] <= 128 * 1024 * 1024
                or type(row['mode']) is not int or row['mode'] not in (0o600, 0o644, 0o700)):
            raise ValueError('Closed reviewed final file attributes required')
        if row['registration']:
            if name not in K.REGISTRATIONS.values() or row['mode'] != 0o644 or row['size'] > 16384:
                raise ValueError('Only the nine bounded fixed registration files allowed')
            data = authority['read_file'](name)
            if type(data) is not bytes or (len(data), _sha(data)) != (row['size'], row['sha256']):
                raise ValueError('Fresh independently admitted registration bytes required')
            registrations[name] = {**row, 'mapped': mapped(name), 'bytes': data}
        else:
            if not any(PurePosixPath(name).is_relative_to(PurePosixPath(root)) for root in K.ROOTS):
                raise ValueError('Owned payload outside fixed roots')
            files[name] = {**row, 'mapped': mapped(name)}
    if len(files) != 250 or len(registrations) != 9:
        raise ValueError('All 250 owned files and nine distinct registrations required')
    directories = {row['path']: {**row, 'mapped': mapped(row['path'])} for row in plan['ownedDirectories']}
    links = {row['path']: {**row, 'mapped': mapped(row['path'])} for row in plan['links']}
    if len(links) != 4 or not set(K.ROOTS).issubset(directories):
        raise ValueError('Both owned roots and all four library aliases required')
    for name, row in directories.items():
        if (not any(PurePosixPath(name).is_relative_to(PurePosixPath(root)) for root in K.ROOTS)
                or (row['uid'], row['gid']) != (0, 0) or row['mode'] not in (0o700, 0o755)):
            raise ValueError('Reviewed owned directory required')
    if set(files) & set(links) or set(files) & set(directories) or set(links) & set(directories):
        raise ValueError('Disjoint owned object names required')
    authority['check']()
    return {'sha256': _sha(raw), 'files': files, 'directories': directories, 'links': links,
            'registrations': registrations, 'roots': {mapped(root) for root in K.ROOTS}}


def _objects(plan, *, final=False):
    result, inventory = {}, {row['mapped']: set() for row in plan['directories'].values()}
    with ExitStack() as stack:
        dirs = _Directories(stack)
        for kind in ('directories', 'files', 'links'):
            for name, row in sorted(plan[kind].items()):
                path = Path(row['mapped'])
                if str(path.parent) in inventory:
                    inventory[str(path.parent)].add(path.name)
                parent = dirs.open(path.parent)
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if kind == 'files':
                    info, _ = _file(dirs, path, row['size'], row['sha256'], row['mode'])
                elif kind == 'directories':
                    O.C._directory(info)
                    allowed = {row['mode']}
                    if str(path) in plan['roots'] and not final:
                        allowed.add(0o700)
                    if stat.S_IMODE(info.st_mode) not in allowed:
                        raise ValueError('Owned directory mode differs from final plan')
                elif (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                      or os.readlink(path.name, dir_fd=parent) != row['target']):
                    raise ValueError('Original root-owned library alias required')
                result[name] = {'kind': kind, 'device': info.st_dev, 'inode': info.st_ino}
        for path, expected in inventory.items():
            if set(os.listdir(dirs.open(path))) != expected:
                raise ValueError('Owned payload inventory changed')
        dirs.check()
    return result


def _journal(stack, path):
    import fcntl
    dirs = _Directories(stack)
    fd = dirs.open(path)
    info = os.fstat(fd)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Private registration transaction required')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)
    return dirs, fd


def _prepare(authority, owned, journal, prefix='/', *, check_runtime=lambda: None):
    A._root_linux()
    check_runtime()
    owned['check']()
    if owned['observe']().get('ownedTargetPayloadsVerified') is not True:
        raise ValueError('Original continuously held owned-copy context required')
    plan = _plan(authority, prefix)
    objects = _objects(plan)
    owned['check']()
    with ExitStack() as stack:
        dirs = _Directories(stack)
        journal = Path(journal)
        parent = dirs.open(journal.parent)
        _absent(parent, journal.name)
        # The source/owned checks above are the final absence-context boundary.
        # Once intent creation begins, only recorded objects can advance this phase.
        try:
            os.mkdir(journal.name, 0o700, dir_fd=parent)
            os.fsync(parent)
            txdirs, txfd = _journal(stack, journal)
            tx = secrets.token_hex(16)
            entries = []
            for index, (name, row) in enumerate(sorted(plan['registrations'].items())):
                check_runtime()
                authority['check']()
                target = Path(row['mapped'])
                directory = dirs.open(target.parent, create=True)
                _absent(directory, target.name)
                staged = '.droiddeck-' + tx + '-' + str(index) + '.pending'
                fd = os.open(staged, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    data = memoryview(row['bytes'])
                    while data:
                        count = os.write(fd, data)
                        if count <= 0:
                            raise OSError('Incomplete registration write')
                        data = data[count:]
                    os.fchmod(fd, 0o644)
                    os.fsync(fd)
                    info = os.fstat(fd)
                    checked, _ = _file(dirs, target.parent / staged, row['size'], row['sha256'], 0o644)
                    if _identity(checked) != _identity(info):
                        raise ValueError('Newly staged registration changed')
                finally:
                    os.close(fd)
                os.fsync(directory)
                directory_info = os.fstat(directory)
                entries.append({'path': name, 'device': info.st_dev, 'inode': info.st_ino,
                    'parentDevice': directory_info.st_dev, 'parentInode': directory_info.st_ino,
                    'parentMode': stat.S_IMODE(directory_info.st_mode)})
            if _objects(plan) != objects:
                raise ValueError('Owned generation changed before durable registration intent')
            authority['check']()
            check_runtime()
            record = _encode({'version': 1, 'kind': 'owned-registration-intent', 'transaction': tx,
                'planSha256': plan['sha256'], 'objects': objects, 'registrations': entries})
            if len(record) > LIMIT:
                raise ValueError('Bounded registration intent required')
            fd = os.open('.intent.pending', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=txfd)
            try:
                view = memoryview(record)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise OSError('Incomplete registration intent write')
                    view = view[count:]
                os.fsync(fd)
                if A._read(fd, len(record)) != record:
                    raise ValueError('Created registration intent bytes changed')
                dirs.check()
                txdirs.check()
                J._rename_new(txfd, '.intent.pending', 'intent.json')
                os.fsync(txfd)
                os.fsync(parent)
            finally:
                os.close(fd)
        except Exception as error:
            raise RegistrationUncertain('Registration intent may exist; preserve targets, stages and journal') from error
    return {'kind': 'prepared-owned-registration', 'planSha256': plan['sha256'], 'servicesActivated': False}


def _finish(authority, journal, prefix='/', *, after_publish=None, check_runtime=lambda: None):
    A._root_linux()
    check_runtime()
    plan = _plan(authority, prefix)
    with ExitStack() as stack:
        dirs, txfd = _journal(stack, journal)
        if set(os.listdir(txfd)) != {'intent.json'}:
            raise RegistrationUncertain('Incomplete or foreign intent inventory; preserve all files')
        fd = os.open('intent.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=txfd)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= LIMIT):
            raise ValueError('Protected immutable registration intent required')
        raw = A._read(fd, LIMIT)
        record = json.loads(raw)
        if (type(record) is not dict or set(record) != {'version', 'kind', 'transaction', 'planSha256', 'objects', 'registrations'}
                or type(record['version']) is not int or record['version'] != 1
                or record['kind'] != 'owned-registration-intent' or _encode(record) != raw
                or type(record['transaction']) is not str or len(record['transaction']) != 32
                or any(c not in '0123456789abcdef' for c in record['transaction'])
                or record['planSha256'] != plan['sha256'] or _encode(_objects(plan)) != _encode(record['objects'])
                or type(record['registrations']) is not list or len(record['registrations']) != 9):
            raise ValueError('Original independently bound registration intent required')
        for entry, name in zip(record['registrations'], sorted(plan['registrations'])):
            if (type(entry) is not dict or set(entry) != {'path', 'device', 'inode', 'parentDevice', 'parentInode', 'parentMode'}
                    or entry['path'] != name or any(type(entry[k]) is not int or entry[k] < 1 for k in entry if k != 'path')):
                raise ValueError('Closed original registration object identities required')

        def registrations():
            observations = []
            for index, entry in enumerate(record['registrations']):
                row = plan['registrations'][entry['path']]
                target = Path(row['mapped'])
                parent = dirs.open(target.parent)
                parent_info = os.fstat(parent)
                if (_identity(parent_info) != (entry['parentDevice'], entry['parentInode'])
                        or stat.S_IMODE(parent_info.st_mode) != entry['parentMode']):
                    raise ValueError('Original registration parent required')
                staged = '.droiddeck-' + record['transaction'] + '-' + str(index) + '.pending'
                present = set()
                for label, name in (('staged', staged), ('final', target.name)):
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    actual, _ = _file(dirs, target.parent / name, row['size'], row['sha256'], 0o644)
                    if _identity(actual) != (entry['device'], entry['inode']):
                        raise ValueError('Foreign registration must not be adopted')
                    present.add(label)
                if present not in ({'staged'}, {'final'}):
                    raise ValueError('Ambiguous or missing owned registration; preserve state')
                observations.append((parent, staged, target.name, present == {'staged'}))
            return observations

        def guard():
            check_runtime()
            authority['check']()
            dirs.check()
            if (_encode(_objects(plan)) != _encode(record['objects']) or A._read(fd, LIMIT) != raw
                    or A._identity(os.fstat(fd)) != A._identity(info)
                    or A._identity(os.stat('intent.json', dir_fd=txfd, follow_symlinks=False)) != A._identity(info)):
                raise ValueError('Original registration ownership or intent changed')
            registrations()

        try:
            guard()
            for name in sorted(K.ROOTS):
                target = plan['directories'][name]['mapped']
                # A separate handle set admits the two recorded 0700 -> 0755 transitions.
                with ExitStack() as chmod_stack:
                    opened = _Directories(chmod_stack)
                    root = opened.open(target)
                    if _identity(os.fstat(root)) != (record['objects'][name]['device'], record['objects'][name]['inode']):
                        raise ValueError('Original exclusively created root required')
                    os.fchmod(root, plan['directories'][name]['mode'])
                    os.fsync(root)
                guard()
            for index, entry in enumerate(record['registrations']):
                guard()
                parent, staged, target, pending = registrations()[index]
                if pending:
                    J._rename_new(parent, staged, target)
                    if after_publish is not None:
                        after_publish(index)
                os.fsync(parent)
                guard()
            if _objects(plan, final=True) != record['objects']:
                raise ValueError('Final owned permissions did not verify')
            guard()
            return {'kind': 'durable-owned-service-registration', 'servicesRegistered': True,
                'registeredFiles': 9, 'ownedPayloadFilesVerified': 250, 'ownedLibraryAliasesVerified': 4,
                'finalPermissionsApplied': True, 'registrationOwnershipVerified': True,
                'registrationPublicationRecoveryVerified': True, 'servicesActivated': False,
                'daemonReloaded': False, 'servicesEnabled': False, 'androidOperationsExecuted': False,
                'runtimeRecoveryVerified': False, 'effectiveServicePolicyVerified': False,
                'installable': False, 'readyForActivation': False}
        except Exception as error:
            raise RegistrationUncertain('Owned registration may have advanced; preserve intent and explicitly inspect/resume') from error


def finalize_new_registration(owned):
    authority = owned['layout']
    _prepare(authority, owned, JOURNAL, check_runtime=_inactive)
    return _finish(authority, JOURNAL, check_runtime=_inactive)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    A._root_linux()
    if len(argv) != 5 or argv[0] not in ('--install-new', '--resume-registration') or argv[-1] != '-':
        raise ValueError('Only new owned installation or explicit registration recovery is supported')
    raw = sys.stdin.buffer.read(O.E.P.C.LIMIT + 1)
    _inactive()
    with ExitStack() as stack:
        if argv[0] == '--install-new':
            dirs = _Directories(stack)
            _absent(dirs.open(Path(JOURNAL).parent), Path(JOURNAL).name)
            result = finalize_new_registration(O.populate_new_targets(stack, argv[1], argv[2], argv[3], raw))
        else:
            result = _finish(K.hold_final_install_layout(stack, argv[1], argv[2], argv[3], raw), JOURNAL, check_runtime=_inactive)
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Owned registration incomplete; preserve all targets and intent, no services activated', file=sys.stderr)
        sys.exit(1)
