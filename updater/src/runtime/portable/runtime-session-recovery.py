# SPDX-License-Identifier: GPL-3.0-only
"""Register bounded container-session recovery after original owned registration."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys

spec = importlib.util.spec_from_file_location('session_registration', Path(__file__).with_name('runtime-registration-transaction.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
JOURNAL = '/var/lib/droiddeck/session-recovery-registration'
FILES = {
    '/etc/systemd/system/droiddeck-session-recovery.service': b'''[Unit]
Description=DroidDeck failed Android session recovery
StartLimitIntervalSec=300s
StartLimitBurst=2

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/
ExecStart=/usr/bin/python3 -I -B /var/lib/droiddeck/gpu-recovery/runtime/portable/fresh-gpu.py --restart-session
TimeoutStartSec=420s
Restart=no
UMask=0077
''',
    '/etc/systemd/system/tesla-waydroid-headless-session.service.d/70-droiddeck-recovery.conf': b'''[Unit]
OnFailure=droiddeck-session-recovery.service

[Service]
RestartMode=direct
''',
    '/etc/systemd/system/tesla-waydroid-headless-session.service.d/90-droiddeck-startup.conf':
        b'[Service]\nTimeoutStartSec=180s\n',
}
RESET_JOURNAL = '/var/lib/droiddeck/session-recovery-reset-registration'
RESET_FILES = {
    '/etc/systemd/system/droiddeck-session-recovery.service.d/80-droiddeck-reset-start-limit.conf':
        b'[Service]\nExecStartPre=/usr/bin/systemctl reset-failed tesla-waydroid-headless-session.service\n',
}


def _paths(prefix, files=FILES):
    return {name: Path(prefix) / name.lstrip('/') for name in files}


def prepare(authority, journal=JOURNAL, prefix='/', *, guard=R._inactive, files=FILES):
    R.A._root_linux()
    guard()
    authority['check']()
    plan = R._sha(authority['read_plan']())
    with ExitStack() as stack:
        dirs = R._Directories(stack)
        parent = dirs.open(Path(journal).parent)
        R._absent(parent, Path(journal).name)
        for path in _paths(prefix, files).values():
            R._absent(dirs.open(path.parent, create=files is RESET_FILES), path.name)
        os.mkdir(Path(journal).name, 0o700, dir_fd=parent)
        os.fsync(parent)
        _txdirs, txfd = R._journal(stack, journal)
        tx, rows = secrets.token_hex(16), []
        for index, (name, path) in enumerate(sorted(_paths(prefix, files).items())):
            guard()
            authority['check']()
            directory = dirs.open(path.parent)
            pending = '.droiddeck-recovery-' + tx + '-' + str(index)
            fd = os.open(pending, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                data = memoryview(files[name])
                while data:
                    count = os.write(fd, data)
                    if count <= 0: raise OSError('Incomplete recovery recipe')
                    data = data[count:]
                os.fchmod(fd, 0o644)
                os.fsync(fd)
                info, raw = R._file(dirs, path.parent / pending, len(files[name]), R._sha(files[name]), 0o644)
                if raw != files[name]: raise ValueError('Original recovery recipe required')
                owner = os.fstat(directory)
                rows.append({'path': name, 'pending': pending, 'device': info.st_dev, 'inode': info.st_ino,
                    'parentDevice': owner.st_dev, 'parentInode': owner.st_ino, 'parentMode': stat.S_IMODE(owner.st_mode)})
                os.fsync(directory)
            finally:
                os.close(fd)
        guard()
        authority['check']()
        raw = R._encode({'version': 1, 'kind': 'session-recovery-registration', 'planSha256': plan, 'rows': rows})
        fd = os.open('.intent.pending', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=txfd)
        try:
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0: raise OSError('Incomplete recovery intent')
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        R.J._rename_new(txfd, '.intent.pending', 'intent.json')
        os.fsync(txfd)
        os.fsync(parent)


def finish(authority, journal=JOURNAL, prefix='/', *, guard=R._inactive, after_publish=None,
           files=FILES, verify_only=False):
    R.A._root_linux()
    if type(verify_only) is not bool:
        raise ValueError('Exact read-only registration verification mode required')
    paths = _paths(prefix, files)
    with ExitStack() as stack:
        dirs, txfd = R._journal(stack, journal)
        if set(os.listdir(txfd)) != {'intent.json'}:
            raise ValueError('Committed recovery intent required; preserve partial state')
        fd = os.open('intent.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=txfd)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        R.J._regular(info)
        raw = R.A._read(fd, R.J.LIMIT)
        record = json.loads(raw)
        if (type(record) is not dict or set(record) != {'version', 'kind', 'planSha256', 'rows'}
                or type(record['version']) is not int or record['version'] != 1
                or record['kind'] != 'session-recovery-registration' or R._encode(record) != raw
                or record['planSha256'] != R._sha(authority['read_plan']())
                or type(record['rows']) is not list or len(record['rows']) != len(files)):
            raise ValueError('Original recovery plan and intent required')

        def observe():
            guard()
            authority['check']()
            dirs.check()
            if (R.A._identity(os.fstat(fd)) != R.A._identity(info)
                    or R.A._identity(os.stat('intent.json', dir_fd=txfd, follow_symlinks=False)) != R.A._identity(info)
                    or R.A._read(fd, R.J.LIMIT) != raw):
                raise ValueError('Original recovery intent changed')
            result = []
            for index, row in enumerate(record['rows']):
                name = sorted(files)[index]
                if (type(row) is not dict or set(row) != {'path', 'pending', 'device', 'inode', 'parentDevice', 'parentInode', 'parentMode'}
                        or row['path'] != name or type(row['pending']) is not str
                        or len(row['pending']) != len('.droiddeck-recovery-') + 34
                        or not row['pending'].startswith('.droiddeck-recovery-')
                        or not all(c in '0123456789abcdef' for c in row['pending'][len('.droiddeck-recovery-'):-2])
                        or not row['pending'].endswith('-' + str(index))
                        or any(type(row[k]) is not int or row[k] <= 0 for k in ('device', 'inode', 'parentDevice', 'parentInode', 'parentMode'))):
                    raise ValueError('Closed recovery ownership row required')
                path = paths[name]
                directory = dirs.open(path.parent)
                parent = os.fstat(directory)
                if (parent.st_dev, parent.st_ino, stat.S_IMODE(parent.st_mode)) != (row['parentDevice'], row['parentInode'], row['parentMode']):
                    raise ValueError('Original recovery registration parent required')
                present = []
                for leaf in (row['pending'], path.name):
                    try:
                        os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    current, _ = R._file(dirs, path.parent / leaf, len(files[name]), R._sha(files[name]), 0o644)
                    if (current.st_dev, current.st_ino) != (row['device'], row['inode']):
                        raise ValueError('Foreign recovery registration refused')
                    present.append(leaf)
                if len(present) != 1:
                    raise ValueError('Ambiguous or missing recovery registration')
                result.append((directory, row['pending'], path.name, present[0] == row['pending']))
            return result

        observations = observe()
        if verify_only and any(row[3] for row in observations):
            raise ValueError('Recovery registration publication incomplete; verification never publishes')
        for index in (() if verify_only else range(len(files))):
            directory, pending, final, needed = observe()[index]
            if needed:
                R.J._rename_new(directory, pending, final)
                if after_publish is not None: after_publish(index)
            os.fsync(directory)
            observe()
        observe()
        return {'kind': 'session-recovery-registration', 'registeredFiles': len(files),
            'registrationRecoveryVerified': True, 'runtimeRecoveryVerified': False,
            'servicesActivated': False, 'servicesEnabled': False, 'daemonReloaded': False,
            'installable': False, 'readyForActivation': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 5 or args[0] not in ('--install', '--resume', '--install-reset', '--resume-reset') or args[-1] != '-':
        raise ValueError('Only fixed session recovery registration commands supported')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    R._inactive()
    with ExitStack() as stack:
        authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)
        R._finish(authority, R.JOURNAL, check_runtime=R._inactive)
        if args[0].endswith('-reset'):
            finish(authority)
            if args[0] == '--install-reset': prepare(authority, RESET_JOURNAL, files=RESET_FILES)
            result = finish(authority, RESET_JOURNAL, files=RESET_FILES)
        else:
            if args[0] == '--install': prepare(authority)
            result = finish(authority)
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Session recovery registration incomplete; preserve all intent/files, no services activated', file=sys.stderr)
        sys.exit(1)
