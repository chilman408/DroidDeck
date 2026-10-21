# SPDX-License-Identifier: GPL-3.0-only
"""Exact rc.1 boot-source update only; never installs Android or replays a token."""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

OLD = '9c34298bfea13d09b782b1abbcd4aad372e58b8abb72f94e03cceea6761d0227'
NEW = '6ba151d8a8ef6f79ab8ecb2b081040b045461adc68915bc889dffca53ee29c82'
TARGET = Path('/var/lib/droiddeck/gpu-recovery/runtime/portable/fresh-gpu.py')
JOURNAL = Path('/var/lib/droiddeck/reboot-hotfix-1')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def directory(stack, path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    stack.callback(os.close, fd)
    for part in Path(path).parts[1:]:
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError('Root-owned non-writable ancestors required')
    return fd


def read(fd, name):
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (0, 0, 0o600) or
                not 0 < before.st_size <= 65536):
            raise ValueError('Exact protected source file required')
        raw = os.read(leaf, 65537)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if (len(raw) != before.st_size or identity(before) != identity(os.fstat(leaf)) or
                identity(before) != identity(os.stat(name, dir_fd=fd, follow_symlinks=False))):
            raise ValueError('Source changed while reading')
        return raw
    finally:
        os.close(leaf)


def write_new(fd, name, raw):
    leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(leaf, 'wb', closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(leaf)
    finally:
        os.close(leaf)
    os.fsync(fd)


def apply(payload, *, target=TARGET, journal=JOURNAL, resume=False, after_replace=None, before=None, after=None):
    old, new = OLD if before is None else before, NEW if after is None else after
    if digest(payload) != new:
        raise ValueError('Exact tested hotfix payload required')
    with ExitStack() as stack:
        parent = directory(stack, target.parent)
        journal_parent = directory(stack, journal.parent)
        if not resume:
            if digest(read(parent, target.name)) != old:
                raise ValueError('Only unchanged rc.1 source may be updated')
            os.mkdir(journal.name, 0o700, dir_fd=journal_parent)
            os.fsync(journal_parent)
        fd = directory(stack, journal)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError('Private original update journal required')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        names = set(os.listdir(fd))
        if not names <= {'original.py', 'payload.py', 'complete.json'}:
            raise ValueError('Foreign update journal contents')
        current = read(parent, target.name)
        if digest(current) not in (old, new):
            raise ValueError('Foreign installed source; no overwrite')
        if 'original.py' not in names:
            if digest(current) != old or names:
                raise ValueError('Original backup missing; no adoption')
            write_new(fd, 'original.py', current)
        if digest(read(fd, 'original.py')) != old:
            raise ValueError('Original backup changed')
        if digest(current) == old:
            if 'complete.json' in names:
                raise ValueError('Completed update must not be replayed')
            if 'payload.py' not in names:
                write_new(fd, 'payload.py', payload)
            if digest(read(fd, 'payload.py')) != new or read(parent, target.name) != current:
                raise ValueError('Update generation changed')
            os.replace('payload.py', target.name, src_dir_fd=fd, dst_dir_fd=parent)
            os.fsync(parent)
            os.fsync(fd)
            if after_replace:
                after_replace()
        elif 'payload.py' in names:
            raise ValueError('Ambiguous payload ownership; no adoption')
        record = json.dumps({'kind': 'rc1-interrupted-boot-source-hotfix', 'before': old, 'after': new}, sort_keys=True).encode() + b'\n'
        if 'complete.json' not in names:
            write_new(fd, 'complete.json', record)
        elif read(fd, 'complete.json') != record:
            raise ValueError('Original completion changed')
        if digest(read(parent, target.name)) != new:
            raise ValueError('Installed payload verification failed')
        return {'kind': 'rc1-interrupted-boot-source-hotfix', 'sourceUpdated': True,
                'androidDataChanged': False, 'servicesStarted': False, 'sha256': new}


DEADLINES = {
    '/etc/systemd/system/tesla-waydroid-headless-session.service.d/90-droiddeck-startup.conf': b'[Service]\nTimeoutStartSec=180s\n',
    '/etc/systemd/system/droiddeck-fresh-gpu-worker@.service.d/90-droiddeck-startup.conf': b'[Service]\nTimeoutStartSec=450s\n',
}


def publish_deadline(path, raw, journal):
    with ExitStack() as stack:
        parent = directory(stack, path.parent)
        fd = directory(stack, journal)
        name = path.parent.name + '.intent.json'
        pending = path.parent.name + '.payload'
        if name not in os.listdir(fd):
            if os.path.lexists(path):
                raise ValueError('Foreign deadline override; no adoption')
            write_new(fd, pending, raw)
            info = os.stat(pending, dir_fd=fd, follow_symlinks=False)
            record = json.dumps({'device': info.st_dev, 'inode': info.st_ino, 'sha256': digest(raw)}, sort_keys=True).encode()
            write_new(fd, name, record)
        record = json.loads(read(fd, name))
        if record.get('sha256') != digest(raw) or set(record) != {'device', 'inode', 'sha256'}:
            raise ValueError('Deadline override journal changed')
        if not os.path.lexists(path):
            info = os.stat(pending, dir_fd=fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (record['device'], record['inode']):
                raise ValueError('Deadline payload ownership changed')
            os.link(pending, path.name, src_dir_fd=fd, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (record['device'], record['inode']):
            raise ValueError('Foreign deadline override identity')
        if pending in os.listdir(fd):
            os.unlink(pending, dir_fd=fd)
            os.fsync(fd)
        if read(parent, path.name) != raw:
            raise ValueError('Deadline override bytes changed')


def main(args):
    if (sys.platform != 'linux' or os.geteuid() != 0 or len(args) != 2 or
            args[0] not in ('--apply', '--resume')):
        raise ValueError('Root Linux --apply/--resume and exact payload path required')
    running = subprocess.run(['/usr/bin/systemctl', 'list-units', '--all', '--output=json', '--no-pager',
                              '--state=active,activating,deactivating', 'droiddeck-fresh-gpu-*.service'],
                             check=True, stdout=subprocess.PIPE, timeout=15).stdout
    if json.loads(running):
        raise ValueError('Stop original GPU watcher and worker before updating their source')
    path = Path(args[1])
    base = TARGET.with_name('gpu-activation.py')
    base_old = '9755e7b4c2b6473d85a09f63ef024d8de9d2a18de2a9d229e20c21171b7cb54f'
    base_new = 'cf17ed639ad8e87dad465f3b2a8190a0987882b3e3fd1b03d183b3276c721dbd'
    root = JOURNAL.with_name('reboot-hotfix-2')
    resume = args[0] == '--resume'
    with ExitStack() as stack:
        source = directory(stack, path.absolute().parent)
        payload, base_payload = read(source, path.name), read(source, 'gpu-activation.py')
        if digest(payload) != NEW or digest(base_payload) != base_new:
            raise ValueError('Exact paired hotfix payloads required')
        if not resume:
            actual = digest(read(directory(stack, TARGET.parent), TARGET.name))
            if actual not in (OLD, '43642e5c70e92aac7229d1711175a00f18caeb54287b39fe4de97f34a6efe786'):
                raise ValueError('Only exact pilot or recorded first-stage fix required')
            if digest(read(directory(stack, base.parent), base.name)) != base_old:
                raise ValueError('Original activation source required')
            if any(os.path.lexists(name) for name in DEADLINES):
                raise ValueError('Existing deadline overrides must not be replaced')
            os.mkdir(root, 0o700)
            os.fsync(directory(stack, root.parent))
            write_new(directory(stack, root), 'before.json', json.dumps({'watcher': actual}).encode())
        actual = json.loads(read(directory(stack, root), 'before.json'))['watcher']
    for name, data, target, old, new in (
            ('watcher', payload, TARGET, actual, NEW), ('activation', base_payload, base, base_old, base_new)):
        sub = root / name
        apply(data, target=target, journal=sub, before=old, after=new, resume=resume and sub.exists())
    for name, raw in DEADLINES.items():
        target = Path(name)
        if not target.parent.exists():
            os.mkdir(target.parent, 0o755)
        publish_deadline(target, raw, root)
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=30)
    print(json.dumps({'kind': 'rc1-interrupted-boot-hotfix', 'sourcesUpdated': True,
                      'androidDataChanged': False, 'servicesStarted': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Boot hotfix incomplete; preserve original journal: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
