# SPDX-License-Identifier: GPL-3.0-only
"""Extend the original owned installation with the independently pinned browser."""
from contextlib import ExitStack
import copy
import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load('runtime-registration-transaction')
PIN = 'fbca122a28633e3e9fb5c80653297dfed1c997bd17fe59b07f01b11691834f7d'
TARGET = '/opt/droiddeck/frontend'
JOURNAL = '/var/lib/droiddeck/browser-install'


def records(raw):
    if type(raw) is not bytes or R._sha(raw) != PIN:
        raise ValueError('Independently reviewed browser manifest required')
    value = json.loads(raw)
    if value['kind'] != 'frontend-component' or value['installable'] is not False or len(value['files']) != 33:
        raise ValueError('Closed browser component required')
    return {row['path']: (row['size'], row['sha256']) for row in value['files']}


def extended(base, held, prefix='/', stage=None):
    plan = copy.deepcopy(base)
    files = {name: held['read'](name) for name in held['files']}
    names = {TARGET}
    for name, raw in files.items():
        logical = TARGET + '/' + name
        mapped = str(Path(stage) / name) if stage else str(Path(prefix) / logical.lstrip('/'))
        plan['files'][logical] = {'path': logical, 'mapped': mapped, 'size': len(raw),
            'sha256': R._sha(raw), 'mode': 0o644, 'uid': 0, 'gid': 0, 'registration': False}
        parent = Path(logical).parent
        while str(parent).startswith(TARGET):
            names.add(str(parent))
            parent = parent.parent
    for name in names:
        mapped = str(Path(stage) / Path(name).relative_to(TARGET)) if stage else str(Path(prefix) / name.lstrip('/'))
        plan['directories'][name] = {'path': name, 'mapped': mapped, 'mode': 0o755, 'uid': 0, 'gid': 0}
    return plan


def read_record(dirs, path):
    path = Path(path)
    parent = dirs.open(path.parent)
    info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
            or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= R.LIMIT):
        raise ValueError('Original private immutable intent required')
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        raw = R.A._read(fd, R.LIMIT)
    finally:
        os.close(fd)
    checked, _ = R._file(dirs, path, len(raw), R._sha(raw), 0o600)
    if R.A._identity(checked) != R.A._identity(info):
        raise ValueError('Original intent generation changed')
    value = json.loads(raw)
    if R._encode(value) != raw:
        raise ValueError('Canonical intent required')
    return value, R._sha(raw)


def original(dirs, base, prefix):
    value, pin = read_record(dirs, Path(prefix) / R.JOURNAL.lstrip('/') / 'intent.json')
    if value['kind'] != 'owned-registration-intent' or value['planSha256'] != base['sha256']:
        raise ValueError('Original independent installation authority required')
    if len(value['registrations']) != 9:
        raise ValueError('All original registrations required')
    for entry, name in zip(value['registrations'], sorted(base['registrations'])):
        row = base['registrations'][name]
        path = Path(row['mapped'])
        info, _ = R._file(dirs, path, row['size'], row['sha256'], 0o644)
        parent = os.fstat(dirs.open(path.parent))
        if (entry['path'] != name or R._identity(info) != (entry['device'], entry['inode'])
                or R._identity(parent) != (entry['parentDevice'], entry['parentInode'])
                or stat.S_IMODE(parent.st_mode) != entry['parentMode']):
            raise ValueError('Original registered inode generation required')
    return value, pin


def rename_new(source_fd, name, target_fd, target):
    if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
        raise ValueError('Atomic same-filesystem browser publication required')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(source_fd, os.fsencode(name), target_fd, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    os.fsync(target_fd)
    os.fsync(source_fd)


def write_new(parent, name, raw, mode):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError(errno.EIO, 'Incomplete browser publication')
            view = view[count:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)


def install(authority, held, *, mode, prefix='/', after_intent=None):
    R.A._root_linux()
    if mode not in ('--install-new', '--resume', '--verify'):
        raise ValueError('Explicit browser publication operation required')
    if mode != '--verify':
        R._inactive()
    base = R._plan(authority, prefix)
    target = Path(prefix) / TARGET.lstrip('/')
    journal = Path(prefix) / JOURNAL.lstrip('/')
    with ExitStack() as stack:
        dirs = R._Directories(stack)
        parent = dirs.open(journal.parent)
        if mode == '--install-new':
            R._finish(authority, Path(prefix) / R.JOURNAL.lstrip('/'), prefix, check_runtime=R._inactive)
            R._absent(dirs.open(target.parent), target.name)
            R._absent(parent, journal.name)
            os.mkdir(journal.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        txdirs, txfd = R._journal(stack, journal)
        original_value, original_pin = original(dirs, base, prefix)
        staged = journal / 'tree'
        if mode == '--install-new':
            held['check']()
            plan = extended(base, held, prefix, staged)
            with ExitStack() as copying:
                copy_dirs = R._Directories(copying)
                for name, row in sorted(plan['directories'].items(), key=lambda item: (item[0].count('/'), item[0])):
                    if name == TARGET or name.startswith(TARGET + '/'):
                        copy_dirs.open(row['mapped'], create=True)
                for name in held['files']:
                    path = staged / name
                    write_new(copy_dirs.open(path.parent), path.name, held['read'](name), 0o644)
            objects = R._objects(plan, final=True)
            if {k: objects[k] for k in original_value['objects']} != original_value['objects']:
                raise ValueError('Original installation changed before browser intent')
            authority['check']()
            held['check']()
            R._inactive()
            record = {'version': 1, 'kind': 'owned-browser-extension', 'manifestSha256': PIN,
                'originalIntentSha256': original_pin, 'objects': objects}
            write_new(txfd, '.intent.pending', R._encode(record), 0o600)
            R.J._rename_new(txfd, '.intent.pending', 'intent.json')
            os.fsync(txfd)
            if after_intent:
                after_intent()
        record, _ = read_record(txdirs, journal / 'intent.json')
        if (set(record) != {'version', 'kind', 'manifestSha256', 'originalIntentSha256', 'objects'}
                or type(record['version']) is not int or record['version'] != 1
                or record['kind'] != 'owned-browser-extension' or record['manifestSha256'] != PIN
                or record['originalIntentSha256'] != original_pin):
            raise ValueError('Original browser extension intent required')
        inventory = set(os.listdir(txfd))
        if inventory not in ({'intent.json', 'tree'}, {'intent.json'}):
            raise ValueError('Uncertain browser state; preserve all files')
        pending = inventory == {'intent.json', 'tree'}
        if pending:
            R._absent(dirs.open(target.parent), target.name)
        plan = extended(base, held, prefix, staged if pending else None)
        observed = R._objects(plan, final=True)
        if (observed != record['objects']
                or {k: observed[k] for k in original_value['objects']} != original_value['objects']):
            raise ValueError('Only original owned browser and installation inodes may advance')
        authority['check']()
        held['check']()
        if mode == '--verify' and pending:
            raise ValueError('Browser publication incomplete')
        if pending:
            R._inactive()
            rename_new(txfd, 'tree', dirs.open(target.parent), target.name)
        if R._objects(extended(base, held, prefix), final=True) != record['objects']:
            raise ValueError('Published browser ownership changed')
        original(dirs, base, prefix)
        return {'kind': 'owned-browser-extension', 'frontendFiles': len(held['files']),
            'manifestSha256': PIN, 'originalInstallationVerified': True, 'browserFilesInstalled': True,
            'servicesActivated': False, 'browserVerified': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 6 or args[0] not in ('--install-new', '--resume', '--verify') or args[-1] != '-':
        raise ValueError('Explicit fixed browser extension required')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)
        account = R.A._account(args[1])
        held = R.A._hold_pinned_tree(stack, args[4], owner=account[1:3], records_reader=records, manifest_limit=65536)
        print(json.dumps(install(authority, held, mode=args[0]), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Browser extension incomplete; preserve original installation and journal; no services activated', file=sys.stderr)
        sys.exit(1)
