# SPDX-License-Identifier: GPL-3.0-only
"""Verify publisher archives and stage image members in a new private directory."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import zipfile

REFERENCE = json.loads((Path(__file__).parents[2] / 'config/portable-image-inputs.json').read_text())
MAX_BYTES = 8 * 1024 ** 3
CHUNK = 1024 ** 2
MAX_ZIP_METADATA = 1024 ** 2
MAX_RECEIPT_BYTES = 65536
RESERVE_INODES = 16
RECEIPT = 'extraction-receipt.json'


class _ZipMetadataReader:
    """Bound zipfile's eager metadata reads before it constructs the member list."""
    def __init__(self, stream):
        self.stream = stream
        self.metadata_remaining = MAX_ZIP_METADATA

    def read(self, size=-1):
        if self.metadata_remaining is not None:
            if size < 0:
                position = self.stream.tell()
                size = self.stream.seek(0, os.SEEK_END) - position
                self.stream.seek(position)
            if size > self.metadata_remaining:
                raise ValueError('Image ZIP metadata read budget exceeded')
        data = self.stream.read(size)
        if self.metadata_remaining is not None:
            self.metadata_remaining -= len(data)
        return data

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()

    def seekable(self):
        return self.stream.seekable()


def normalize_plan(plan):
    value = copy.deepcopy(REFERENCE if plan is None else plan)
    if (not isinstance(value, dict) or value.keys() != REFERENCE.keys()
            or any(value[key] != expected for key, expected in REFERENCE.items() if key != 'inputs')
            or not isinstance(value['inputs'], dict) or set(value['inputs']) != {'system', 'vendor'}):
        raise ValueError('Image policy rejected')
    for role in ('system', 'vendor'):
        item, original = value['inputs'][role], REFERENCE['inputs'][role]
        if (not isinstance(item, dict) or item.keys() != original.keys()
                or any(item[key] != original[key] for key in ('filename', 'url', 'member'))
                or not isinstance(item['sha256'], str) or len(item['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in item['sha256'])):
            raise ValueError('Image source pin rejected')
    return value


def directory_chain(directory):
    absolute = Path(os.path.abspath(directory))
    for ancestor in reversed((absolute, *absolute.parents)):
        if not stat.S_ISDIR(ancestor.lstat().st_mode):
            raise ValueError('Unsafe image directory ancestor')
    return absolute


def identity(info):
    # Windows fstat and lstat disagree on deprecated ctime after a file rewrite.
    change_time = info.st_ctime_ns if os.name == 'posix' else None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, change_time, info.st_nlink)


def regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_BYTES:
        raise ValueError('Unsafe or oversized image archive')


def unchanged(filename, stream, before):
    opened, current = os.fstat(stream.fileno()), filename.lstat()
    regular(opened)
    regular(current)
    if identity(before) != identity(opened) or identity(before) != identity(current):
        raise ValueError('Image archive changed')


def inspect_archive(stack, filename, item, max_image_bytes):
    before = filename.lstat()
    regular(before)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    stream = stack.enter_context(os.fdopen(os.open(filename, flags), 'rb'))
    unchanged(filename, stream, before)
    digest = hashlib.sha256()
    total = 0
    while block := stream.read(CHUNK):
        total += len(block)
        if total > before.st_size:
            raise ValueError('Image archive changed')
        digest.update(block)
    unchanged(filename, stream, before)
    if total != before.st_size or digest.hexdigest() != item['sha256']:
        raise ValueError('Image archive digest mismatch')
    stream.seek(0)
    reader = _ZipMetadataReader(stream)
    archive = stack.enter_context(zipfile.ZipFile(reader))
    # Image payloads retain the existing chunk/size/CRC bounds, not this metadata cap.
    reader.metadata_remaining = None
    members = archive.infolist()
    if len(members) != 1:
        raise ValueError('Unexpected image archive members')
    member = members[0]
    mode = member.external_attr >> 16
    if (member.filename != item['member'] or member.orig_filename != item['member'] or member.is_dir()
            or not stat.S_ISREG(mode) or member.flag_bits & 1
            or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or not 0 < member.file_size <= max_image_bytes):
        raise ValueError('Unsafe image archive member')
    return filename, stream, before, archive, member


def open_directory(stack, directory):
    if os.name != 'posix':
        return None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(directory.anchor, flags)
    stack.callback(os.close, descriptor)
    for name in directory.parts[1:]:
        descriptor = os.open(name, flags, dir_fd=descriptor)
        stack.callback(os.close, descriptor)
    before, opened = directory.lstat(), os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        raise ValueError('Extraction directory changed')
    return descriptor


def write_private(filename, *, directory_fd=None):
    name = filename.name if directory_fd is not None else filename
    return os.fdopen(os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                             0o600, dir_fd=directory_fd), 'w+b')


def verify_written(target, size, expected_digest):
    before = os.fstat(target.fileno())
    target.seek(0)
    digest, total = hashlib.sha256(), 0
    while block := target.read(CHUNK):
        total += len(block)
        if total > size:
            raise ValueError('Output hash mismatch')
        digest.update(block)
    after = os.fstat(target.fileno())
    if total != size or digest.hexdigest() != expected_digest or identity(before) != identity(after):
        raise ValueError('Output hash mismatch or changed file')
    return after


def check_capacity(parent, parent_fd, sizes, reserve_bytes):
    if (type(sizes) is not tuple or len(sizes) != 2
            or any(type(size) is not int or not 0 < size <= MAX_BYTES for size in sizes)
            or type(reserve_bytes) is not int or not 0 <= reserve_bytes <= MAX_BYTES):
        raise ValueError('Bounded image capacity plan required')
    if parent_fd is None:
        # The non-POSIX development path has no descriptor/inode observation.
        if shutil.disk_usage(parent).free < sum(sizes) + MAX_RECEIPT_BYTES + 4096 + reserve_bytes:
            raise ValueError('Insufficient extraction space')
        return
    if type(parent_fd) is not int or parent_fd < 0:
        raise ValueError('Held extraction parent descriptor required')
    info = os.fstatvfs(parent_fd)
    values = tuple(getattr(info, name) for name in
        ('f_frsize', 'f_blocks', 'f_bfree', 'f_bavail', 'f_files', 'f_ffree', 'f_favail', 'f_flag'))
    if (any(type(value) is not int or value < 0 for value in values)
            or not 0 < info.f_frsize <= 1024 ** 2
            or not 0 <= info.f_bavail <= info.f_bfree <= info.f_blocks
            or not 0 <= info.f_favail <= info.f_ffree <= info.f_files or info.f_files == 0
            or info.f_flag & getattr(os, 'ST_RDONLY', 1)):
        raise ValueError('Writable known extraction filesystem capacity required')
    rounded = lambda size: (size + info.f_frsize - 1) // info.f_frsize
    # Two images, one bounded receipt inode (pending/final are hardlinks), one directory.
    blocks = sum(rounded(size) for size in (*sizes, MAX_RECEIPT_BYTES, reserve_bytes)) + 1
    if info.f_bavail < blocks or info.f_favail < 4 + RESERVE_INODES:
        raise ValueError('Insufficient extraction space or inodes')


def extract_images(source, output, *, plan=None, home=None, max_image_bytes=MAX_BYTES, reserve_bytes=64 * 1024 ** 2):
    plan = normalize_plan(plan)
    if (type(max_image_bytes) is not int or not 0 < max_image_bytes <= MAX_BYTES
            or type(reserve_bytes) is not int or not 0 <= reserve_bytes <= MAX_BYTES):
        raise ValueError('Invalid extraction bounds')
    source = directory_chain(source)
    home = directory_chain(Path.home() if home is None else home)
    output = Path(os.path.abspath(output))
    if output == home or not output.is_relative_to(home) or os.path.lexists(output):
        raise ValueError('Output must be new and below home')
    parent = directory_chain(output.parent)
    if os.name == 'posix':
        owner = os.geteuid()
        if parent.stat().st_uid != owner or parent.stat().st_mode & 0o022:
            raise ValueError('Unsafe output parent ownership')
    with ExitStack() as stack:
        parent_fd = open_directory(stack, parent)
        archives = [(role, inspect_archive(stack, source / item['filename'], item, max_image_bytes))
                    for role, item in plan['inputs'].items()]
        check_capacity(parent, parent_fd, tuple(record[-1].file_size for _, record in archives), reserve_bytes)
        os.mkdir(output.name if parent_fd is not None else output, 0o700, dir_fd=parent_fd)
        if parent_fd is not None:
            created = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
            output_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            stack.callback(os.close, output_fd)
            opened = os.fstat(output_fd)
            if (created.st_dev, created.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError('Extraction directory changed')
        else:
            output_fd, opened = None, output.lstat()
        output_identity = (opened.st_dev, opened.st_ino)
        images = []
        written = []

        def check_output(expected):
            current = output.lstat()
            opened = os.fstat(output_fd) if output_fd is not None else current
            if (not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino) != output_identity
                    or (opened.st_dev, opened.st_ino) != output_identity
                    or sorted(os.listdir(output_fd if output_fd is not None else output)) != sorted(expected)
                    or (os.name == 'posix' and (opened.st_uid != owner or stat.S_IMODE(opened.st_mode) != 0o700))):
                raise ValueError('Extraction output changed')
            for name, target, before in written:
                path_info = os.stat(name if output_fd is not None else output / name,
                                    dir_fd=output_fd, follow_symlinks=False)
                file_info = os.fstat(target.fileno())
                if (not stat.S_ISREG(path_info.st_mode) or path_info.st_nlink != 1
                        or identity(path_info) != identity(before) or identity(file_info) != identity(before)
                        or (os.name == 'posix' and (path_info.st_uid != owner or stat.S_IMODE(path_info.st_mode) != 0o600))):
                    raise ValueError('Extracted image changed')

        for role, (_, _, _, archive, member) in archives:
            size = 0
            digest = hashlib.sha256()
            with archive.open(member) as image:
                check_output([record[0] for record in written])
                target = stack.enter_context(write_private(output / member.filename, directory_fd=output_fd))
                while block := image.read(CHUNK):
                    size += len(block)
                    if size > member.file_size or size > max_image_bytes:
                        raise ValueError('Image output exceeded declared size')
                    target.write(block)
                    digest.update(block)
                if size != member.file_size:
                    raise ValueError('Image output length mismatch')
                target.flush()
                os.fsync(target.fileno())
                written.append((member.filename, target, verify_written(target, size, digest.hexdigest())))
            images.append({'role': role, 'member': member.filename, 'size': size, 'sha256': digest.hexdigest(),
                           'archiveSha256': plan['inputs'][role]['sha256']})
        for _, (filename, stream, before, _, _) in archives:
            unchanged(filename, stream, before)
        check_output(['system.img', 'vendor.img'])
        receipt = {'kind': 'external-image-extraction', 'installable': False, 'redistribute': False,
                   'release': plan['release'], 'extracted': True,
                   'publisherDigestMatched': all(item['sha256'] == REFERENCE['inputs'][role]['sha256']
                                                for role, item in plan['inputs'].items()), 'images': images}
        pending = output / 'extraction-receipt.partial'
        encoded = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode('utf-8')
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise ValueError('Bounded extraction receipt required')
        target = stack.enter_context(write_private(pending, directory_fd=output_fd))
        target.write(encoded)
        target.flush()
        os.fsync(target.fileno())
        written.append((pending.name, target, verify_written(target, len(encoded), hashlib.sha256(encoded).hexdigest())))
        check_output(['system.img', 'vendor.img', pending.name])
        if output_fd is None:
            target.close()
        # Linux links the verified held inode, not a replaceable pending pathname.
        source_link = f'/proc/self/fd/{target.fileno()}' if output_fd is not None else pending
        os.link(source_link,
                RECEIPT if output_fd is not None else output / RECEIPT,
                dst_dir_fd=output_fd, follow_symlinks=output_fd is not None)
        # Publication is the commit point; leftover temporary metadata is harmless.
        try:
            os.unlink(pending.name if output_fd is not None else pending, dir_fd=output_fd)
        except OSError:
            pass
        return receipt


def main(args):
    if sys.platform != 'linux' or os.geteuid() == 0 or len(args) != 2:
        raise ValueError('Unprivileged Linux image staging requires SOURCE NEW_OUTPUT')
    print(json.dumps(extract_images(args[0], args[1]), sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        print('Image extraction rejected; partial output is not installable', file=sys.stderr)
        sys.exit(1)
