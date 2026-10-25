# SPDX-License-Identifier: GPL-3.0-only
"""Exact-content .90 browser-only AUD backport. Never restarts a service."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

TARGET = Path('/home/hilman/teslaandroid-waydroid/frontend/js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js')
CANDIDATE = Path('/home/hilman/worker-aud-candidate-20261024ie.js')
BACKUP = Path('/var/lib/teslaandroid-waydroid/backups/webcodecs-aud-20261024ie')
BEFORE = 'dcc3b7a718503d87d82c8e1a5331cb0bed162e733dab5d01b65aa02017f36beb'
AFTER = '2a5dd7f06f8cf3676af467590484e2e3df32ae1d2fb1a082e09ced91e01e9c41'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, *, root=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144
                or info.st_uid not in ((0,) if root else (0, 1000)) or info.st_mode & 0o022):
            raise ValueError('Protected regular JavaScript or receipt required')
        raw = os.read(fd, 262145)
        if len(raw) != info.st_size:
            raise ValueError('File changed during read')
        return info, raw
    finally:
        os.close(fd)


def save_new(path, raw, *, uid=0, gid=0, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise ValueError('Incomplete new file retained for inspection')
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(info, raw):
    pending = TARGET.with_name('pending-webcodecs-aud-20261024ie-worker.js')
    save_new(pending, raw, uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))
    current = TARGET.lstat()
    fields = ('st_dev', 'st_ino', 'st_mtime_ns', 'st_size')
    if any(getattr(current, key) != getattr(info, key) for key in fields):
        raise ValueError('Foreign concurrent change retained')
    os.replace(pending, TARGET)
    sync_directory(TARGET.parent)


def main(mode):
    if os.geteuid() != 0:
        raise ValueError('Root needed only for exact browser file and private backup')
    info, current = read(TARGET)
    if mode == '--apply':
        _, candidate = read(CANDIDATE)
        if sha(current) != BEFORE or sha(candidate) != AFTER or BACKUP.exists() or BACKUP.is_symlink():
            raise ValueError('Exact unused baseline required; never replay apply')
        BACKUP.mkdir(mode=0o700)
        save_new(BACKUP / 'worker.before.js', current)
        save_new(BACKUP / 'prepared.json', json.dumps({'before': BEFORE, 'after': AFTER}).encode())
        sync_directory(BACKUP)
        sync_directory(BACKUP.parent)
        replace(info, candidate)
        if sha(read(TARGET)[1]) != AFTER:
            raise ValueError('Inspect retained backup; no automatic replay')
        save_new(BACKUP / 'applied.json', json.dumps({'workerSha256': AFTER}).encode())
        sync_directory(BACKUP)
        print(json.dumps({'applied': True, 'workerSha256': AFTER, 'browserOnly': True}))
    elif mode == '--restore':
        backup_info = BACKUP.lstat()
        if not stat.S_ISDIR(backup_info.st_mode) or backup_info.st_uid != 0 or backup_info.st_mode & 0o077:
            raise ValueError('Original private backup directory required')
        _, original = read(BACKUP / 'worker.before.js', root=True)
        if sha(original) != BEFORE or sha(current) != AFTER:
            raise ValueError('Foreign state retained; no guessed rollback')
        replace(info, original)
        if sha(read(TARGET)[1]) != BEFORE:
            raise ValueError('Restoration hash verification failed; inspect backup')
        save_new(BACKUP / 'restored.json', json.dumps({'workerSha256': BEFORE}).encode())
        sync_directory(BACKUP)
        print(json.dumps({'restored': True, 'workerSha256': BEFORE, 'browserOnly': True}))
    else:
        raise ValueError('--apply or --restore required')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise ValueError('One explicit mode required')
    main(sys.argv[1])
