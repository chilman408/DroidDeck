# SPDX-License-Identifier: GPL-3.0-only
"""Copy pinned external images or runtime artifacts into new protected staging."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

PARENT = Path('/var/lib/droiddeck/image-staging')
PINS = {
    'system.img': (1802956800, '582629757cb379f3e6699d1681e3358d2c6f82cf08b8ce1f1c1584e22d409aff'),
    'vendor.img': (721117184, '63e002329543ef42a67cc6cfcd40655060ccdd6d00f87bbcc7510683cf07cf72'),
}
ARTIFACT_PARENT = Path('/var/lib/droiddeck/artifact-staging')
ARTIFACT_PINS = {
    'tesla-android-x86-touch': (991688, 'bf37f6b9c94446b037d352446c77035754d0e7a0fccb62a1d59da1a9c4cd79dd'),
    'tesla-android-x86-api': (3149808, '7a938269a9437e94e84bd9d97727c6283c511cdb6dfce300ccce7bbb94879754'),
    'tesla-android-x86-display-latest': (1006488, 'e90c13b4ee680b9fb4971b1701e8bc124b35a07996f5863cc98653b25db5d7d8'),
    'location-bridge.jar': (4973, '809acb4b5d4c86ece5d2378dc3dd9eb2b47bc4781d756012047570c61a493cbc'),
    'logical-capture.jar': (3114, 'a12fa7ecdb27dbbd63656b8dea1b2f9e12d2aae75632b73afe86ff2d2761c044'),
    'libglibutil.so.1.0.80': (85664, 'adf21a840c7754cb61f82e444ae73c1f410abe4f1427e35f74fe45fa99170d90'),
    'libgbinder.so.1.1.45': (164192, 'd473613f9c57921113d3cefe1cbc99676476270db756b52b5aa407c1ece06cae'),
    'scrcpy-server-v4.0': (732226, '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a'),
}
CHUNK = 1024 ** 2
RESERVE = 64 * 1024 ** 2


class PublicationUncertain(RuntimeError):
    """The ready receipt could not be durably withdrawn after an I/O failure."""


def stage_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value):
        raise ValueError('Invalid stage name')
    return value


def canonical(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or '\x00' in value:
        raise ValueError('Invalid source path')
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts:
        raise ValueError('Canonical absolute path required')
    return path


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_nlink, info.st_mode, info.st_uid, info.st_gid)


def protected(info, mode=None):
    if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022 or (mode is not None and stat.S_IMODE(info.st_mode) != mode):
        raise ValueError('Unsafe protected staging ownership or mode')


def open_chain(stack, path, *, root_owned=False):
    chain = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    previous = None
    for index in range(len(path.parts)):
        current = Path(*path.parts[:index + 1])
        before = current.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError('Unsafe directory ancestor')
        fd = os.open(current.anchor if previous is None else current.name, flags, dir_fd=previous)
        stack.callback(os.close, fd)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('Directory changed')
        if root_owned:
            protected(opened)
        chain.append((current, fd, opened.st_dev, opened.st_ino))
        previous = fd
    return chain


def check_chain(chain, *, root_owned=False):
    for path, fd, device, inode in chain:
        for info in (path.lstat(), os.fstat(fd)):
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode):
                raise ValueError('Directory changed')
            if root_owned:
                protected(info)


def regular(info, size):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
        raise ValueError('Unsafe image type, links or size')


def check_image(directory_fd, name, fd, before, *, private=False):
    for info in (os.stat(name, dir_fd=directory_fd, follow_symlinks=False), os.fstat(fd)):
        regular(info, before.st_size)
        if identity(info) != identity(before):
            raise ValueError('Image changed')
        if private:
            protected(info, 0o600)


def digest_fd(fd, size):
    os.lseek(fd, 0, os.SEEK_SET)
    digest, total = hashlib.sha256(), 0
    while block := os.read(fd, min(CHUNK, size - total + 1)):
        total += len(block)
        if total > size:
            raise ValueError('Image exceeded pinned size')
        digest.update(block)
    if total != size:
        raise ValueError('Image length mismatch')
    return digest.hexdigest()


def write_all(fd, block):
    view = memoryview(block)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError('Incomplete image write')
        view = view[written:]


def create_private(stack, directory_fd, name):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    stack.callback(os.close, fd)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, 0o600)
    return fd


def copy_image(stack, source_fd, output_fd, name, pin):
    size, expected = pin
    target_fd = create_private(stack, output_fd, name)
    os.lseek(source_fd, 0, os.SEEK_SET)
    digest, total = hashlib.sha256(), 0
    while block := os.read(source_fd, min(CHUNK, size - total + 1)):
        total += len(block)
        if total > size:
            raise ValueError('Image exceeded pinned size')
        digest.update(block)
        write_all(target_fd, block)
    if total != size or digest.hexdigest() != expected:
        raise ValueError('Copied image digest mismatch')
    os.fsync(target_fd)
    before = os.fstat(target_fd)
    if digest_fd(target_fd, size) != expected:
        raise ValueError('Staged image digest mismatch')
    check_image(output_fd, name, target_fd, before, private=True)
    return target_fd, before


def _stage_images(source, parent, name, pins, *, artifacts=False):
    # Only the private fixture API accepts a pin/parent argument; CLI policy is fixed.
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux staging required')
    source, parent = Path(canonical(source)), Path(canonical(str(parent)))
    name = stage_name(name)
    policy = ARTIFACT_PINS if artifacts else PINS
    if (set(pins) != set(policy) or any(type(size) is not int or not 0 < size <= policy[n][0]
            or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
            for n, (size, digest) in pins.items())):
        raise ValueError('Invalid image pins')
    with ExitStack() as stack:
        source_chain = open_chain(stack, source)
        parent_chain = open_chain(stack, parent, root_owned=True)
        source_fd, parent_fd = source_chain[-1][1], parent_chain[-1][1]
        images = {}
        for image_name, (size, expected) in pins.items():
            before = os.stat(image_name, dir_fd=source_fd, follow_symlinks=False)
            regular(before, size)
            fd = os.open(image_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
            stack.callback(os.close, fd)
            check_image(source_fd, image_name, fd, before)
            if digest_fd(fd, size) != expected:
                raise ValueError('Publisher image digest mismatch')
            check_image(source_fd, image_name, fd, before)
            images[image_name] = (fd, before)
        check_chain(source_chain)
        check_chain(parent_chain, root_owned=True)
        space = os.fstatvfs(parent_fd)
        if space.f_bavail * space.f_frsize < sum(pin[0] for pin in pins.values()) + RESERVE:
            raise ValueError('Insufficient staging space')
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        output_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        stack.callback(os.close, output_fd)
        os.fchown(output_fd, 0, 0)
        os.fchmod(output_fd, 0o700)
        output_before = os.fstat(output_fd)
        outputs = {}

        def check_stage(expected_names):
            check_chain(source_chain)
            check_chain(parent_chain, root_owned=True)
            for info in (os.stat(name, dir_fd=parent_fd, follow_symlinks=False), os.fstat(output_fd)):
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (output_before.st_dev, output_before.st_ino):
                    raise ValueError('Staging directory changed')
                protected(info, 0o700)
            if sorted(os.listdir(output_fd)) != sorted(expected_names):
                raise ValueError('Unexpected staging entries')
            for image_name, (fd, before) in images.items():
                check_image(source_fd, image_name, fd, before)
            for image_name, (fd, before) in outputs.items():
                check_image(output_fd, image_name, fd, before, private=True)

        for image_name, pin in pins.items():
            check_stage(outputs)
            outputs[image_name] = copy_image(stack, images[image_name][0], output_fd, image_name, pin)
            check_stage(outputs)
        receipt = {'kind': 'protected-external-image-stage', 'installable': False,
                   'publisherPinsMatched': pins == PINS, 'readyForInitialization': pins == PINS,
                   'androidOperationsExecuted': False, 'redistribute': False,
                   'images': [{'name': n, 'size': p[0], 'sha256': p[1]} for n, p in pins.items()]}
        if artifacts:
            receipt = {'kind': 'protected-runtime-artifact-stage', 'installable': False,
                       'buildPinsMatched': pins == ARTIFACT_PINS,
                       'readyForActivation': False, 'androidOperationsExecuted': False,
                       'redistribute': False,
                       'artifacts': [{'name': n, 'size': p[0], 'sha256': p[1]} for n, p in pins.items()]}
        encoded = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
        receipt_fd = create_private(stack, output_fd, 'ready.partial')
        write_all(receipt_fd, encoded)
        os.fsync(receipt_fd)
        receipt_before = os.fstat(receipt_fd)
        if digest_fd(receipt_fd, len(encoded)) != hashlib.sha256(encoded).hexdigest():
            raise ValueError('Staging receipt changed')
        check_image(output_fd, 'ready.partial', receipt_fd, receipt_before, private=True)
        check_stage([*outputs, 'ready.partial'])
        os.fsync(output_fd)
        os.fsync(parent_fd)
        check_stage([*outputs, 'ready.partial'])
        check_image(output_fd, 'ready.partial', receipt_fd, receipt_before, private=True)
        # Publish the held receipt inode exclusively; incomplete stages have no ready.json.
        os.link(f'/proc/self/fd/{receipt_fd}', 'ready.json', dst_dir_fd=output_fd, follow_symlinks=True)
        try:
            os.unlink('ready.partial', dir_fd=output_fd)
            os.fsync(output_fd)
        except OSError:
            try:
                os.unlink('ready.json', dir_fd=output_fd)
                os.fsync(output_fd)
            except OSError as error:
                raise PublicationUncertain('Staging publication uncertain; inspect protected stage before use') from error
            raise
        return receipt


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux image staging requires SOURCE NEW_STAGE_NAME')
    if len(args) == 3 and args[0] == '--artifacts':
        return _stage_images(args[1], ARTIFACT_PARENT, args[2], ARTIFACT_PINS, artifacts=True)
    if len(args) != 2 or args[0].startswith('--'):
        raise ValueError('Root Linux staging requires SOURCE NEW_STAGE_NAME')
    return _stage_images(args[0], PARENT, args[1], PINS)


def run_cli(args):
    try:
        receipt = main(args)
    except PublicationUncertain:
        print('Staging publication uncertain; inspect protected stage before use', file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError):
        print('Protected staging did not complete; inspect existing or partial output before use', file=sys.stderr)
        return 1
    try:
        print(json.dumps(receipt, sort_keys=True, indent=2), flush=True)
    except OSError:
        print('Protected stage committed; receipt output failed; inspect protected stage', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(run_cli(sys.argv[1:]))
