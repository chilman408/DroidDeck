# SPDX-License-Identifier: GPL-3.0-only
"""Read-only source bootstrap; installation and activation entry remain unavailable."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import types

ROOT = Path('/opt/droiddeck/machine-gpu-runtime')
PINS = {
    'runtime/portable/promote-images.py': (253820, 'e4f67d1c43c8a81486b1e6de02a157b3a5f52d1665730053cfff7afbc8a75616'),
    'runtime/portable/stage-images.py': (12509, '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a'),
    'runtime/portable/machine-gpu-supervision.py': (13262, '34cb29f173e2f0ab65f69db7605e87a1e9bb49c789c8a7f435a5369fd54326c3'),
    'runtime/portable/effective-units.py': (5285, '5df4aeb90df7d10fcf2d052438b53465b411a475ecc0ebc37afdddb89d2a6f4e'),
    'tools/waydroid-layer-property.py': (5278, '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f'),
    'tools/waydroid-gpu-health-policy.py': (1127, '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e'),
    'runtime/portable/machine-gpu-peer-policy.py': (8491, '78e01a854a7abff59337332cae2c25a4582ca843964a28fac9c0a7c18d42d976'),
    'runtime/portable/machine-gpu-lifecycle.py': (9370, '06abee4309cbcb885c1000b05be74a5b62512d4c7c423c0beaf179c450a43e7c'),
    'runtime/portable/machine-gpu-lifecycle-state.py': (16461, '3d3053db581d07aa8f9cd35519e1995c8053ec804a63b31a75092628994485da'),
}
INVENTORY = {
    '': {'runtime', 'tools'}, 'runtime': {'portable'},
    'runtime/portable': {'promote-images.py', 'stage-images.py', 'machine-gpu-supervision.py',
        'effective-units.py', 'machine-gpu-peer-policy.py', 'machine-gpu-lifecycle.py', 'machine-gpu-lifecycle-state.py'},
    'tools': {'waydroid-layer-property.py', 'waydroid-gpu-health-policy.py'},
}
ROLES = {'copy': 'runtime/portable/stage-images.py', 'recovery': 'runtime/portable/promote-images.py',
    'supervision': 'runtime/portable/machine-gpu-supervision.py', 'units': 'runtime/portable/effective-units.py',
    'health': 'tools/waydroid-gpu-health-policy.py', 'peers': 'runtime/portable/machine-gpu-peer-policy.py',
    'lifecycle': 'runtime/portable/machine-gpu-lifecycle.py', 'state': 'runtime/portable/machine-gpu-lifecycle-state.py'}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_nlink, info.st_mode, info.st_uid, info.st_gid)


def _directory(info, private=False):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o22
            or (private and (info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o700))):
        raise ValueError('Protected bootstrap directory required')


def _bytes(fd, size):
    chunks, offset = [], 0
    while offset <= size:
        chunk = os.pread(fd, min(1024 * 1024, size + 1 - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b''.join(chunks)


def hold_bootstrap_sources(stack, root=ROOT):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux source bootstrap required')
    value = str(root)
    path = PurePosixPath(value)
    if ('\x00' in value or not 0 < len(value) <= 4096 or not path.is_absolute()
            or value.startswith('//') or str(path) != value or '..' in path.parts or value == '/'):
        raise ValueError('Canonical nonroot bootstrap source directory required')
    root = Path(value)
    chain, directories, records = [], {}, {}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def opened(name, parent=None, private=False):
        fd = os.open(name, flags, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        _directory(info, private)
        chain.append((parent, name, fd, info.st_dev, info.st_ino, private))
        return fd

    current = opened('/')
    for index, name in enumerate(path.parts[1:]):
        current = opened(name, current, private=index == len(path.parts) - 2)
    directories[''] = current
    for relative in ('runtime', 'runtime/portable', 'tools'):
        parent, _, name = relative.rpartition('/')
        directories[relative] = opened(name, directories[parent], private=True)

    def file_check(parent, name, fd, info):
        current = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_uid != 0
                or current.st_gid != 0 or stat.S_IMODE(current.st_mode) != 0o600
                or _identity(current) != _identity(info) or _identity(named) != _identity(info)):
            raise ValueError('Original root-private bootstrap source required')

    def check():
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('Root Linux source bootstrap required')
        for parent, name, fd, device, inode, private in chain:
            info = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _directory(info, private)
            _directory(named, private)
            if (info.st_dev, info.st_ino) != (device, inode) or (named.st_dev, named.st_ino) != (device, inode):
                raise ValueError('Bootstrap source ancestor generation changed')
        for relative, directory in directories.items():
            if set(os.listdir(directory)) != INVENTORY[relative]:
                raise ValueError('Closed bootstrap source inventory required')
        for relative, (parent, name, fd, info, _raw) in records.items():
            file_check(parent, name, fd, info)
            raw = _bytes(fd, PINS[relative][0])
            if len(raw) != PINS[relative][0] or hashlib.sha256(raw).hexdigest() != PINS[relative][1]:
                raise ValueError('Held bootstrap source bytes changed')
            file_check(parent, name, fd, info)

    check()
    for relative, (size, digest) in PINS.items():
        parent, name = relative.rsplit('/', 1)
        directory = directories[parent]
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if info.st_size != size:
            raise ValueError('Fixed bootstrap source size required')
        file_check(directory, name, fd, info)
        raw = _bytes(fd, size)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Bootstrap source differs from independent fixed recipe')
        file_check(directory, name, fd, info)
        records[relative] = directory, name, fd, info, raw
    check()

    def load(role):
        if type(role) is not str or role not in ROLES:
            raise ValueError('Closed bootstrap module role required')
        check()
        relative = ROLES[role]
        module = types.ModuleType('droiddeck_bootstrapped_' + role)
        module.__file__ = str(root / relative)
        # No source module executes until the complete independent recipe is held.
        exec(compile(records[relative][4], module.__file__, 'exec'), module.__dict__)
        check()
        return module

    copier = load('copy')
    return {'check': check, 'load': load, 'copier': copier, 'policy': {
        'kind': 'protected-machine-gpu-bootstrap-sources', 'bootstrapComponentSourcesVerified': True,
        'componentSourcesVerified': True, 'componentCount': len(PINS), 'installedRootMatched': root == ROOT,
        'installedEntryVerified': False, 'installedRecipeVerified': False, 'effectiveGraphVerified': False,
        'independentRecoveryArmed': False, 'servicesActivated': False, 'androidOperationsExecuted': False,
        'installable': False, 'readyForActivation': False}}


def main(argv=None):
    raise ValueError('Production machine GPU bootstrap entry is not available')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Machine GPU bootstrap unavailable; no services or Android data changed', file=sys.stderr)
        sys.exit(1)
