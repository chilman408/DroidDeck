# SPDX-License-Identifier: GPL-3.0-only
"""Pinned read-only bootstrap launcher; production start modes deliberately refuse."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import types

BOOTSTRAP = Path('/opt/droiddeck/machine-gpu-bootstrap.py')
ROOT = Path('/opt/droiddeck/machine-gpu-runtime')
BOOTSTRAP_PIN = (8079, '2eb30a22d93a4b897536f3654016710429aadc5c3b60fe4a6c389db79b3d3bf3')


def _canonical(value):
    value = str(value)
    path = PurePosixPath(value)
    if ('\x00' in value or not 0 < len(value) <= 4096 or not path.is_absolute()
            or value.startswith('//') or str(path) != value or '..' in path.parts or value == '/'):
        raise ValueError('Canonical nonroot source path required')
    return Path(value)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_nlink, info.st_mode, info.st_uid, info.st_gid)


def _directory(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o22:
        raise ValueError('Protected bootstrap ancestor required')


def _read(fd):
    chunks, offset = [], 0
    while offset <= BOOTSTRAP_PIN[0]:
        chunk = os.pread(fd, BOOTSTRAP_PIN[0] + 1 - offset, offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    raw = b''.join(chunks)
    if (len(raw), hashlib.sha256(raw).hexdigest()) != BOOTSTRAP_PIN:
        raise ValueError('Bootstrap differs from independent launcher pin')
    return raw


def hold_runtime_sources(stack, *, bootstrap=BOOTSTRAP, root=ROOT):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux pinned bootstrap launcher required')
    bootstrap, root = _canonical(bootstrap), _canonical(root)
    chain = []
    current = None
    for name in bootstrap.parent.parts:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        _directory(info)
        chain.append((current, name, fd, info.st_dev, info.st_ino))
        current = fd
    fd = os.open(bootstrap.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
    stack.callback(os.close, fd)
    info = os.fstat(fd)

    def check_bootstrap():
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('Root Linux pinned bootstrap launcher required')
        for parent, name, directory, device, inode in chain:
            held = os.fstat(directory)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _directory(held)
            _directory(named)
            if (held.st_dev, held.st_ino) != (device, inode) or (named.st_dev, named.st_ino) != (device, inode):
                raise ValueError('Held bootstrap ancestor changed')
        def file_check():
            held = os.fstat(fd)
            named = os.stat(bootstrap.name, dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISREG(held.st_mode) or held.st_nlink != 1 or held.st_uid != 0 or held.st_gid != 0
                    or stat.S_IMODE(held.st_mode) != 0o600 or held.st_size != BOOTSTRAP_PIN[0]
                    or _identity(held) != _identity(info) or _identity(named) != _identity(info)):
                raise ValueError('Original private pinned bootstrap required')
        file_check()
        raw = _read(fd)
        file_check()
        return raw

    raw = check_bootstrap()
    module = types.ModuleType('droiddeck_pinned_bootstrap')
    module.__file__ = str(bootstrap)
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    check_bootstrap()
    sources = module.hold_bootstrap_sources(stack, root)

    def check():
        check_bootstrap()
        sources['check']()
        check_bootstrap()

    def load(role):
        check()
        result = sources['load'](role)
        check()
        return result

    check()
    return {'check': check, 'load': load, 'copier': sources['copier'], 'policy': {
        **sources['policy'], 'kind': 'protected-machine-gpu-launcher-sources',
        'bootstrapSourceVerified': True, 'verifiedSourceCount': 10,
        'bootstrapPathMatched': bootstrap == BOOTSTRAP}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--verify-sources']:
        raise ValueError('Production GPU start modes are unavailable')
    with ExitStack() as stack:
        sources = hold_runtime_sources(stack)
        sources['check']()
        print(json.dumps(sources['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Machine GPU entry refused; no services or Android data changed', file=sys.stderr)
        sys.exit(1)
