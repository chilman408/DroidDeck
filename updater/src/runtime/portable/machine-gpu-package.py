# SPDX-License-Identifier: GPL-3.0-only
"""Hold, stage and install source files only; service execution remains unavailable."""
from contextlib import ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import sys
from types import MappingProxyType

PINS = MappingProxyType({
    'machine-gpu-entry.py': (4890, '534bcdd629dca1c48f92663be42d5708d313ba62f7b37dc9155ace18db50ca70'),
    'machine-gpu-bootstrap.py': (8079, '2eb30a22d93a4b897536f3654016710429aadc5c3b60fe4a6c389db79b3d3bf3'),
    'machine-gpu-runtime/runtime/portable/promote-images.py': (253820, 'e4f67d1c43c8a81486b1e6de02a157b3a5f52d1665730053cfff7afbc8a75616'),
    'machine-gpu-runtime/runtime/portable/stage-images.py': (12509, '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a'),
    'machine-gpu-runtime/runtime/portable/machine-gpu-supervision.py': (13262, '34cb29f173e2f0ab65f69db7605e87a1e9bb49c789c8a7f435a5369fd54326c3'),
    'machine-gpu-runtime/runtime/portable/effective-units.py': (5285, '5df4aeb90df7d10fcf2d052438b53465b411a475ecc0ebc37afdddb89d2a6f4e'),
    'machine-gpu-runtime/tools/waydroid-layer-property.py': (5278, '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f'),
    'machine-gpu-runtime/tools/waydroid-gpu-health-policy.py': (1127, '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e'),
    'machine-gpu-runtime/runtime/portable/machine-gpu-peer-policy.py': (8491, '78e01a854a7abff59337332cae2c25a4582ca843964a28fac9c0a7c18d42d976'),
    'machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle.py': (9370, '06abee4309cbcb885c1000b05be74a5b62512d4c7c423c0beaf179c450a43e7c'),
    'machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle-state.py': (16461, '3d3053db581d07aa8f9cd35519e1995c8053ec804a63b31a75092628994485da'),
})
MANIFEST = 'bundle-manifest.json'
FALSE_FLAGS = ('installedEntryVerified', 'effectiveGraphVerified', 'independentRecoveryArmed',
               'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted')
STAGING_PARENT = PurePosixPath('/var/lib/droiddeck/machine-gpu-source-staging')
INSTALL_PARENT = PurePosixPath('/opt')
COPY_STAGING_PARENT = PurePosixPath('/opt/droiddeck-machine-gpu-source-staging')


def _manifest():
    value = {'version': 1, 'product': 'DroidDeck', 'kind': 'machine-gpu-source-component',
             **dict.fromkeys(FALSE_FLAGS, False),
             'files': [{'path': name, 'size': size, 'sha256': digest} for name, (size, digest) in sorted(PINS.items())]}
    return (json.dumps(value, indent=2) + '\n').encode('ascii')


def _inventory():
    result = {'': {MANIFEST}}
    for name in PINS:
        parts = name.split('/')
        for index, part in enumerate(parts):
            result.setdefault('/'.join(parts[:index]), set()).add(part)
    return {key: frozenset(value) for key, value in result.items()}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_nlink, info.st_mode, info.st_uid, info.st_gid)


def _directory(info, private, owner=(0, 0)):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner[0]) or info.st_mode & 0o022
            or (private and ((info.st_uid, info.st_gid) != owner or stat.S_IMODE(info.st_mode) != 0o700))):
        raise ValueError('Original protected package directory required')


def _bytes(fd, size):
    chunks, offset = [], 0
    while offset <= size:
        block = os.pread(fd, min(1024 * 1024, size + 1 - offset), offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    return b''.join(chunks)


def _root_linux():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux held package verification required')


def _canonical(root):
    value = str(root)
    path = PurePosixPath(value)
    if ('\x00' in value or not 0 < len(value) <= 4096 or not path.is_absolute()
            or value.startswith('//') or str(path) != value or '..' in path.parts or value == '/'):
        raise ValueError('Canonical nonroot package path required')
    return path


def _source_owner(value):
    if (type(value) is not tuple or len(value) != 2
            or any(type(identifier) is not int or not 0 <= identifier < 2**31 for identifier in value)
            or (value != (0, 0) and (value[0] == 0 or value[1] == 0))):
        raise ValueError('Independent exact source UID/GID pair required')
    return value


def hold_source_package(stack, root, *, owner=(0, 0)):
    """Caller independently supplies trusted verifier and owner, never receipt-derived."""
    _root_linux()
    owner = _source_owner(owner)
    path = _canonical(root)
    chain, directories, records = [], {}, {}
    inventory = _inventory()
    manifest = _manifest()
    pins = {**PINS, MANIFEST: (len(manifest), hashlib.sha256(manifest).hexdigest())}

    def opened(name, parent=None, private=False):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        _directory(info, private, owner)
        namespace = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        chain.append((parent, name, fd, info.st_dev, info.st_ino, private, namespace))
        return fd

    current = opened('/')
    for index, name in enumerate(path.parts[1:]):
        current = opened(name, current, private=index == len(path.parts) - 2)
    directories[''] = current
    for relative in sorted((key for key in inventory if key), key=lambda key: (key.count('/'), key)):
        parent, _, name = relative.rpartition('/')
        directories[relative] = opened(name, directories[parent], private=True)

    def file_check(parent, name, fd, original):
        for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_uid, info.st_gid) != owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or _identity(info) != _identity(original)):
                raise ValueError('Original owner-bound private package file required')

    def check():
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('Root Linux held package verification required')
        for parent, name, fd, device, inode, private, namespace in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                _directory(info, private, owner)
                if ((info.st_dev, info.st_ino) != (device, inode)
                        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != namespace):
                    raise ValueError('Package ancestor generation changed')
        for relative, fd in directories.items():
            if set(os.listdir(fd)) != inventory[relative]:
                raise ValueError('Closed independent package inventory required')
        for relative, (parent, name, fd, info, _raw) in records.items():
            file_check(parent, name, fd, info)
            raw = _bytes(fd, pins[relative][0])
            if (len(raw), hashlib.sha256(raw).hexdigest()) != pins[relative]:
                raise ValueError('Held package differs from independent recipe')
            file_check(parent, name, fd, info)

    check()
    for relative, pin in sorted(pins.items()):
        parent, _, name = relative.rpartition('/')
        directory = directories[parent]
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if info.st_size != pin[0]:
            raise ValueError('Independent package file size required')
        file_check(directory, name, fd, info)
        raw = _bytes(fd, pin[0])
        if (len(raw), hashlib.sha256(raw).hexdigest()) != pin:
            raise ValueError('Package differs from independent fixed recipe')
        file_check(directory, name, fd, info)
        records[relative] = directory, name, fd, info, raw
    check()

    def read(relative):
        if type(relative) is not str or relative not in records:
            raise ValueError('Closed held package file required')
        check()
        raw = records[relative][4]
        check()
        return raw

    def directory_generation(fd):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink

    generations = tuple(sorted(
        [(relative, directory_generation(fd)) for relative, fd in directories.items()]
        + [(relative, _identity(record[3])) for relative, record in records.items()]))
    check()

    def flush():
        check()
        for record in records.values():
            os.fsync(record[2])
        for relative in sorted(directories, key=lambda key: (key.count('/'), key), reverse=True):
            os.fsync(directories[relative])
        check()

    return {'check': check, 'read': read, 'flush': flush, 'generations': generations,
            'files': tuple(sorted(records)), 'policy': {
        'kind': 'protected-machine-gpu-source-package', 'sourcePackageVerified': True,
        'entryRecipeVerified': True, 'payloadSourcesVerified': len(PINS), 'sourceOwnerMatched': True,
        'rootPrivateSourcePackage': owner == (0, 0), **dict.fromkeys(FALSE_FLAGS, False)}}


class SourcePublicationUncertain(RuntimeError):
    """A new attempt/final directory may exist; preserve it and inspect before retry."""


def _write_private(stack, directory, name, raw):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    stack.callback(os.close, fd)
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Incomplete new package write')
        view = view[count:]
    os.fsync(fd)
    return fd, os.fstat(fd), raw


def _rename_new(directory, old, new):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(directory, old.encode('ascii'), directory, new.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def publish_source_package(stack, source, parent, name, *, source_owner=(0, 0)):
    """Internal API: one new package in an independently prepared EMPTY private parent."""
    _root_linux()
    source_owner = _source_owner(source_owner)
    parent = _canonical(parent)
    if type(name) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', name):
        raise ValueError('Closed new source package name required')
    package = hold_source_package(stack, source, owner=source_owner)
    payload = {relative: package['read'](relative) for relative in package['files']}
    chain, directories, expected, files = [], {}, {}, {}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = None
    for index, part in enumerate(parent.parts):
        fd = os.open(part, flags, dir_fd=current)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        private = index == len(parent.parts) - 1
        _directory(info, private)
        chain.append((current, part, fd, info.st_dev, info.st_ino, private))
        current = fd
    parent_fd = current
    parent_entries = set()

    def check_created():
        _root_linux()
        for ancestor, leaf, fd, device, inode, private in chain:
            for info in (os.fstat(fd), os.stat(leaf, dir_fd=ancestor, follow_symlinks=False)):
                _directory(info, private)
                if (info.st_dev, info.st_ino) != (device, inode):
                    raise ValueError('Original source publication directory changed')
        if set(os.listdir(parent_fd)) != parent_entries:
            raise ValueError('Dedicated empty/new source publication parent required')
        for relative, fd in directories.items():
            if set(os.listdir(fd)) != expected[relative]:
                raise ValueError('Unexpected source publication entry')
        for directory, leaf, fd, original, raw in files.values():
            for after_read in (False, True):
                if after_read and _bytes(fd, len(raw)) != raw:
                    raise ValueError('New source publication bytes changed')
                for info in (os.fstat(fd), os.stat(leaf, dir_fd=directory, follow_symlinks=False)):
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_gid != 0
                            or stat.S_IMODE(info.st_mode) != 0o600 or _identity(info) != _identity(original)):
                        raise ValueError('Original new source publication file required')

    def guard():
        package['check']()
        check_created()

    guard()
    space = os.fstatvfs(parent_fd)
    if space.f_bavail * space.f_frsize < sum(map(len, payload.values())) + 1024 * 1024:
        raise ValueError('Insufficient new source publication space')
    attempt = '.source-attempt-' + secrets.token_hex(16)
    guard()
    try:
        os.mkdir(attempt, 0o700, dir_fd=parent_fd)
        parent_entries.add(attempt)

        def new_directory(ancestor, leaf):
            before = os.stat(leaf, dir_fd=ancestor, follow_symlinks=False)
            fd = os.open(leaf, flags, dir_fd=ancestor)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            _directory(info, True)
            if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or os.listdir(fd):
                raise ValueError('Original empty source attempt required')
            chain.append((ancestor, leaf, fd, info.st_dev, info.st_ino, True))
            return fd

        root_index = len(chain)
        directories[''] = new_directory(parent_fd, attempt)
        expected[''] = set()
        inventory = _inventory()
        for relative in sorted((key for key in inventory if key), key=lambda key: (key.count('/'), key)):
            guard()
            relative_parent, _, leaf = relative.rpartition('/')
            directory = directories[relative_parent]
            os.mkdir(leaf, 0o700, dir_fd=directory)
            directories[relative] = new_directory(directory, leaf)
            expected[relative] = set()
            expected[relative_parent].add(leaf)
            guard()

        def copied(relative):
            guard()
            relative_parent, _, leaf = relative.rpartition('/')
            directory = directories[relative_parent]
            fd, info, raw = _write_private(stack, directory, leaf, payload[relative])
            files[relative] = directory, leaf, fd, info, raw
            expected[relative_parent].add(leaf)
            guard()

        for relative in sorted(PINS):
            copied(relative)
        for relative in sorted(directories, key=lambda key: key.count('/'), reverse=True):
            os.fsync(directories[relative])
        copied(MANIFEST)  # The fixed inert manifest is complete only after all payloads verify.
        os.fsync(directories[''])
        os.fsync(parent_fd)
        guard()
        _rename_new(parent_fd, attempt, name)
        # Anchor the known successful rename before any fallible stat/flush/check.
        old = chain[root_index]
        chain[root_index] = old[0], name, *old[2:]
        parent_entries = {name}
        os.fsync(parent_fd)
        guard()
        result = hold_source_package(stack, parent / name)
        check_created()
        result['check']()
    except (OSError, ValueError, RuntimeError) as error:
        raise SourcePublicationUncertain('Source publication may exist; preserve dedicated parent and inspect before retry') from error

    def check():
        check_created()
        result['check']()
        check_created()

    def read(relative):
        check()
        raw = result['read'](relative)
        check()
        return raw

    return {'check': check, 'read': read, 'files': result['files'], 'policy': {
        **result['policy'], 'newSourcePackagePublished': True, 'unprivilegedSourceAdmitted': source_owner != (0, 0)}}


def _source_account(user):
    import pwd
    try:
        account = pwd.getpwnam(user)
    except KeyError as error:
        raise ValueError('Existing independently resolved source account required') from error
    owner = _source_owner((account.pw_uid, account.pw_gid))
    home = account.pw_dir
    if (account.pw_name != user or owner == (0, 0) or type(home) is not str
            or not re.fullmatch('/home/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', home) or home.endswith('.')):
        raise ValueError('Exact unprivileged source account and runtime home required')
    return user, *owner, home


def stage_user_source(user, source, stage):
    """Staging only: fixed base/pre-created empty slot; never install or activate."""
    _root_linux()
    if (type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root'
            or type(stage) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', stage)):
        raise ValueError('Closed source account and staging slot required')
    source = _canonical(source)
    account = _source_account(user)
    home = PurePosixPath(account[3])
    if not source.is_relative_to(home) or source == home:
        raise ValueError('Source package beneath independently resolved account home required')
    may_exist = False
    try:
        with ExitStack() as stack:
            try:
                result = publish_source_package(stack, source, STAGING_PARENT / stage, 'package', source_owner=account[1:3])
            except SourcePublicationUncertain:
                may_exist = True  # A cleanup failure must not hide the original partial-write uncertainty.
                raise
            may_exist = True
            result['check']()
            if _source_account(user) != account:
                raise ValueError('Source account changed during publication')
            policy = {**result['policy'], 'sourceAccountMatched': True, 'stagingOnly': True}
        return policy
    except (OSError, ValueError, RuntimeError) as error:
        if may_exist:
            raise SourcePublicationUncertain('Source staging may exist; preserve slot and inspect before retry') from error
        raise


def _install_parent(stack, path, *, private):
    path = _canonical(path)
    chain, current = [], None
    for index, part in enumerate(path.parts):
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        is_private = private and index == len(path.parts) - 1
        _directory(info, is_private)
        namespace = info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode
        chain.append((current, part, fd, is_private, namespace))
        current = fd

    def check():
        _root_linux()
        for parent, name, fd, is_private, original in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                _directory(info, is_private)
                if (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != original:
                    raise ValueError('Original source installation parent required')

    check()
    return current, check


def _rename_between(source_fd, source, target_fd, target):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = library.renameat2
    except AttributeError as error:
        raise OSError('Atomic no-replace source installation unavailable') from error
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(source_fd, source.encode('ascii'), target_fd, target.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _mount_id(fd):
    info = os.open('/proc/self/fdinfo/' + str(fd), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(info, 4097)
    finally:
        os.close(info)
    matches = re.findall(rb'^mnt_id:\s+([1-9][0-9]*)$', raw, re.MULTILINE)
    if len(raw) > 4096 or len(matches) != 1:
        raise ValueError('Independent Linux descriptor mount identity required')
    return int(matches[0])


def install_staged_sources(stage):
    """Consume a root-staged package into ABSENT /opt/droiddeck, never start services."""
    _root_linux()
    if type(stage) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', stage):
        raise ValueError('Closed source staging slot required')
    slot = STAGING_PARENT / stage
    may_exist = False
    try:
        with ExitStack() as stack:
            source_fd, check_source = _install_parent(stack, slot, private=True)
            target_fd, check_target = _install_parent(stack, INSTALL_PARENT, private=False)
            mount = _mount_id(source_fd)
            if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev or mount != _mount_id(target_fd):
                raise ValueError('Same-filesystem atomic source installation required')

            def parents(moved):
                check_source()
                check_target()
                if _mount_id(source_fd) != mount or _mount_id(target_fd) != mount:
                    raise ValueError('Original same-mount source installation required')
                if set(os.listdir(source_fd)) != (set() if moved else {'package'}):
                    raise ValueError('Exact dedicated source staging slot required')
                if not moved:
                    try:
                        os.stat('droiddeck', dir_fd=target_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise ValueError('New source installation destination must be absent')

            parents(False)
            source = hold_source_package(stack, slot / 'package')
            generations = source['generations']
            source['flush']()
            parents(False)
            source['check']()
            # Even a failed/ambiguous rename must never become a blind retry or rollback.
            may_exist = True
            _rename_between(source_fd, 'package', target_fd, 'droiddeck')
            parents(True)
            os.fsync(target_fd)
            os.fsync(source_fd)
            installed = hold_source_package(stack, INSTALL_PARENT / 'droiddeck')
            if installed['generations'] != generations:
                raise ValueError('Original staged source generations required after installation')
            installed['check']()
            parents(True)
            policy = {**installed['policy'], 'sourceFilesInstalled': True, 'newDirectoryOnly': True}
        return policy
    except (OSError, ValueError, RuntimeError) as error:
        if may_exist:
            raise SourcePublicationUncertain('Source installation may exist; preserve slot and destination; inspect before retry') from error
        raise


def _install_published_copy(stack, source, copy_slot, copy_fd, target_fd, parents, *, source_owner=(0, 0)):
    may_exist = False
    try:
        published = publish_source_package(stack, source, copy_slot, 'package', source_owner=source_owner)
        may_exist = True
        published['check']()
        copied = hold_source_package(stack, copy_slot / 'package')
        generations = copied['generations']
        published['check']()
        parents({'package'})
        _rename_between(copy_fd, 'package', target_fd, 'droiddeck')
        parents(set(), installed=True)
        os.fsync(target_fd)
        os.fsync(copy_fd)
        installed = hold_source_package(stack, INSTALL_PARENT / 'droiddeck')
        if installed['generations'] != generations:
            raise ValueError('Original created copy generations required after installation')
        installed['check']()
        parents(set(), installed=True)
        return installed
    except (OSError, ValueError, RuntimeError) as error:
        if may_exist:
            raise SourcePublicationUncertain('Source copy or installation may exist; preserve slots and destination; inspect before retry') from error
        raise


def copy_staged_sources(stage):
    """Explicit cross-mount copy into a pre-created private destination slot, then publish."""
    _root_linux()
    if type(stage) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', stage):
        raise ValueError('Closed source-copy staging slot required')
    slot, copy_slot = STAGING_PARENT / stage, COPY_STAGING_PARENT / stage
    may_exist = False
    try:
        with ExitStack() as stack:
            source_fd, check_source = _install_parent(stack, slot, private=True)
            copy_fd, check_copy = _install_parent(stack, copy_slot, private=True)
            target_fd, check_target = _install_parent(stack, INSTALL_PARENT, private=False)
            source_mount, copy_mount = _mount_id(source_fd), _mount_id(copy_fd)
            if (os.fstat(copy_fd).st_dev != os.fstat(target_fd).st_dev or copy_mount != _mount_id(target_fd)):
                raise ValueError('Copy staging slot must share the installation mount')
            source = None

            def parents(entries, *, installed=False):
                check_source()
                if source is not None:
                    source['check']()
                check_copy()
                check_target()
                if (_mount_id(source_fd) != source_mount or _mount_id(copy_fd) != copy_mount
                        or _mount_id(target_fd) != copy_mount):
                    raise ValueError('Original source and copy destination mounts required')
                if set(os.listdir(source_fd)) != {'package'} or set(os.listdir(copy_fd)) != entries:
                    raise ValueError('Exact dedicated source/copy staging slots required')
                if not installed:
                    try:
                        os.stat('droiddeck', dir_fd=target_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise ValueError('New source installation destination must be absent')

            parents(set())
            source = hold_source_package(stack, slot / 'package')
            try:
                installed = _install_published_copy(stack, slot / 'package', copy_slot, copy_fd, target_fd, parents)
            except SourcePublicationUncertain:
                may_exist = True
                raise
            may_exist = True
            installed['check']()
            source['check']()
            parents(set(), installed=True)
            policy = {**installed['policy'], 'sourceFilesInstalled': True, 'newDirectoryOnly': True,
                      'stagedSourceRetained': True, 'installationMethod': 'copy-then-publish'}
        return policy
    except (OSError, ValueError, RuntimeError) as error:
        if may_exist:
            raise SourcePublicationUncertain('Source copy or installation may exist; preserve slots and destination; inspect before retry') from error
        raise


def install_user_sources(user, source, stage):
    """Fresh-host source phase only: resolve user, prepare NEW slot, copy and publish."""
    _root_linux()
    if (type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root'
            or type(stage) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', stage)):
        raise ValueError('Closed source account and NEW staging slot required')
    source = _canonical(source)
    account = _source_account(user)
    home = PurePosixPath(account[3])
    if not source.is_relative_to(home) or source == home or COPY_STAGING_PARENT.parent != INSTALL_PARENT:
        raise ValueError('Independent account home and fixed installation namespace required')
    may_exist = False
    try:
        with ExitStack() as stack:
            target_fd, check_target = _install_parent(stack, INSTALL_PARENT, private=False)
            mount = _mount_id(target_fd)
            children = []

            def absent(parent, name):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise ValueError('New source installation destination and slot must be absent')

            absent(target_fd, 'droiddeck')
            original = hold_source_package(stack, source, owner=account[1:3])

            def check_inputs(*, installed=False):
                check_target()
                original['check']()
                if _source_account(user) != account:
                    raise ValueError('Source account changed during installation')
                if _mount_id(target_fd) != mount:
                    raise ValueError('Original installation mount required')
                for parent, name, fd, expected in children:
                    for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                        _directory(info, True)
                        if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) != expected:
                            raise ValueError('Original private preparation directory required')
                    if _mount_id(fd) != mount:
                        raise ValueError('Prepared source slot must share the installation mount')
                if not installed:
                    absent(target_fd, 'droiddeck')

            def opened(parent, name, *, create=False):
                nonlocal may_exist
                if create:
                    check_inputs()
                    may_exist = True
                    os.mkdir(name, 0o700, dir_fd=parent)
                    os.fsync(parent)
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                _directory(info, True)
                if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or (create and os.listdir(fd)):
                    raise ValueError('Original empty new preparation directory required')
                children.append((parent, name, fd, (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)))
                check_inputs()
                return fd

            check_inputs()
            try:
                os.stat(COPY_STAGING_PARENT.name, dir_fd=target_fd, follow_symlinks=False)
            except FileNotFoundError:
                base_fd = opened(target_fd, COPY_STAGING_PARENT.name, create=True)
            else:
                base_fd = opened(target_fd, COPY_STAGING_PARENT.name)
            absent(base_fd, stage)
            copy_fd = opened(base_fd, stage, create=True)

            def parents(entries, *, installed=False):
                check_inputs(installed=installed)
                if set(os.listdir(copy_fd)) != entries:
                    raise ValueError('Exact new private source staging slot required')

            parents(set())
            result = _install_published_copy(stack, source, COPY_STAGING_PARENT / stage, copy_fd, target_fd,
                                             parents, source_owner=account[1:3])
            result['check']()
            parents(set(), installed=True)
            policy = {**result['policy'], 'sourceFilesInstalled': True, 'newDirectoryOnly': True,
                      'sourceAccountMatched': True, 'sourceInputRetained': True, 'installationMethod': 'user-copy-then-publish'}
        return policy
    except (OSError, ValueError, RuntimeError) as error:
        if may_exist:
            raise SourcePublicationUncertain('Source preparation or installation may exist; preserve directories and inspect before retry') from error
        raise


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 4 and argv[0] == '--stage-user-package':
        print(json.dumps(stage_user_source(*argv[1:]), sort_keys=True))
        return
    if len(argv) == 4 and argv[0] == '--install-user-sources':
        print(json.dumps(install_user_sources(*argv[1:]), sort_keys=True))
        return
    if len(argv) == 2 and argv[0] == '--install-staged-sources':
        print(json.dumps(install_staged_sources(argv[1]), sort_keys=True))
        return
    if len(argv) == 2 and argv[0] == '--copy-staged-sources':
        print(json.dumps(copy_staged_sources(argv[1]), sort_keys=True))
        return
    if len(argv) != 2 or argv[0] != '--verify-package':
        raise ValueError('Only package verification, staging or new source installation is available')
    with ExitStack() as stack:
        package = hold_source_package(stack, argv[1])
        package['check']()
        print(json.dumps(package['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except SourcePublicationUncertain:
        print('Source publication may exist; preserve slot and destination; inspect before retry; no activation performed', file=sys.stderr)
        sys.exit(3)
    except (OSError, ValueError, RuntimeError):
        print('Machine GPU package refused; no services activated', file=sys.stderr)
        sys.exit(1)
