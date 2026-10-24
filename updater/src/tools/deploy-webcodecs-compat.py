# SPDX-License-Identifier: GPL-3.0-only
"""New fixed .90 browser-only compatibility backport; no runtime/Android operations."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/opt/teslaandroid-waydroid/webcodecs-compat-20261024hv')
BACKUP = Path('/var/lib/teslaandroid-waydroid/backups/webcodecs-compat-20261024hv')
FRONTEND = Path('/home/hilman/teslaandroid-waydroid/frontend')
FILES = [
    ('manager', 'js/display/display-renderer-manager.js', 'c0b5c865fc6a3c37f576e3d1137372d5d75b52a9d42db10b4d5419a81a0a7c53'),
    ('settings', 'js/settings/settings-controller.js', 'aab0751ac4906f8295ef162379b5d65f793389faf589a799ce0aa4d9e9d9198a'),
    ('worker', 'js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js', '8808b505f4c9aacc6548eb1d845ff59cd95b2464bf7941b4fd7bd71fbcab6f94'),
]
# The candidate hashes are fixed below from reviewed public assets, not live data.
AFTER = {
    'manager': 'fe5cb641ca2b25c024229ecf1757d339fb8697642572b012dc684c135f78716b',
    'settings': 'd450ec729362bd17a16d4f3dbb37048f97d548cfe841972388a4d1985707395e',
    'worker': 'dcc3b7a718503d87d82c8e1a5331cb0bed162e733dab5d01b65aa02017f36beb',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144
                or info.st_uid not in ((0,) if private else (0, 1000)) or info.st_mode & 0o022):
            raise ValueError('Protected regular code required')
        raw = os.read(fd, 262145)
        if len(raw) != info.st_size:
            raise ValueError('Code changed during read')
        return info, raw
    finally:
        os.close(fd)


def save_new(path, raw, *, uid=0, gid=0, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise ValueError('Incomplete public code retained for inspection')
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(path, expected, target, label):
    info, raw = read(path)
    if sha(raw) != expected:
        raise ValueError('Foreign live public code retained')
    pending = path.with_name('pending-webcodecs-compat-20261024hv-' + label)
    save_new(pending, target, uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))
    current = path.lstat()
    if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
        raise ValueError('Live public code generation changed')
    os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def inspect():
    candidates = {}
    for name, relative, before in FILES:
        _info, raw = read(ROOT / (name + '-candidate.js'), private=True)
        if sha(raw) != AFTER[name] or sha(read(FRONTEND / relative)[1]) != before:
            raise ValueError('Exact new compatibility source and baseline required')
        candidates[name] = raw
    if BACKUP.exists() or BACKUP.is_symlink():
        raise ValueError('Previous compatibility attempt retained; never repeat apply')
    return candidates


def restore():
    originals = {}
    # Verify every original and live file before restoring any code.
    for name, relative, before in FILES:
        raw = read(BACKUP / (name + '.js'), private=True)[1]
        live = sha(read(FRONTEND / relative)[1])
        if sha(raw) != before or live not in (before, AFTER[name]):
            raise ValueError('Foreign code retained; no guessed recovery')
        originals[name] = raw
    for name, relative, before in reversed(FILES):
        if sha(read(FRONTEND / relative)[1]) == AFTER[name]:
            replace(FRONTEND / relative, AFTER[name], originals[name], name + '-restore')


def main(args):
    if (args not in (['--prepare'], ['--apply'], ['--restore']) or sys.platform != 'linux'
            or os.getuid() != 0 or Path(__file__) != ROOT / 'deploy-webcodecs-compat.py'
            or Path('/etc/hostname').read_text().strip() != 'Waydroid-TeslaAndroid-Test'):
        raise ValueError('Fixed protected root preview operation required')
    read(Path(__file__), private=True)
    if args == ['--restore']:
        restore()
        print(json.dumps({'browserCodeRestored': True, 'runtimeRestarted': False}))
        return
    candidates = inspect()
    if args == ['--prepare']:
        print(json.dumps({'readOnlyCompatibilityPreflight': True, 'files': 3}))
        return
    BACKUP.mkdir(mode=0o700)
    for name, relative, _before in FILES:
        save_new(BACKUP / (name + '.js'), read(FRONTEND / relative)[1])
    save_new(BACKUP / 'receipt.json', (json.dumps({'profile': 'webcodecs-compat-20261024hv', 'after': AFTER}, sort_keys=True) + '\n').encode())
    try:
        for name, relative, before in FILES:
            replace(FRONTEND / relative, before, candidates[name], name)
    except Exception:
        restore()
        raise
    print(json.dumps({'compatibilityInstalled': True, 'rendererSettingRemoved': True,
                      'webCodecsDefault': False, 'runtimeRestarted': False}))


if __name__ == '__main__':
    main(sys.argv[1:])
