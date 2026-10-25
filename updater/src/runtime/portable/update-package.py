# SPDX-License-Identifier: GPL-3.0-only
"""Review and privately stage runtime updates; never install or execute a payload."""
import argparse
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile

BASE = {'version': '0.1.0-rc.2',
        'sourceSha256': 'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28'}
PLATFORM = {'distribution': 'ubuntu', 'version': '26.04', 'architecture': 'x86_64'}
COMPONENTS = {'frontend': '/opt/droiddeck/frontend',
              'gateway': '/opt/droiddeck/runtime/preview-gateway',
              'display': '/opt/droiddeck/runtime/waydroid-display'}
PREFIX = 'droiddeck-update/'
MANIFEST = PREFIX + 'manifest.json'
MAX_FILE, MAX_TOTAL, MAX_ARCHIVE, MAX_FILES = 16 * 1024**2, 64 * 1024**2, 80 * 1024**2, 4096
EXTENSIONS = {'.js', '.cjs', '.mjs', '.json', '.html', '.css', '.wasm', '.node', '.so',
              '.png', '.svg', '.ico', '.otf', '.ttf', '.woff', '.woff2', '.txt', '.md'}
RUNTIME_SCRIPTS = {'compositor-h264.py': 'gateway', 'logical-screencap.sh': 'display', 'native-stop.py': 'display'}
DEPENDENCY_METADATA = {'.npmignore', '.travis.yml', '.gitattributes', '.auto-changelog', 'codecov.yml', 'README.markdown'}
PRIVATE = {'.git', '.ssh', '.config', '.env', 'known_hosts', 'authorized_keys',
           'id_ed25519', 'id_rsa', 'credentials.json', 'key.pem', 'cert.pem'}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate JSON key')
        result[name] = value
    return result


def document(raw):
    value = json.loads(raw, object_pairs_hook=unique)
    if encode(value) != raw:
        raise ValueError('Canonical JSON required')
    return value


def digest(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Explicit trusted SHA256 required, not an unsigned download checksum')
    return value


def version(value):
    if type(value) is not str:
        raise ValueError('String release version required')
    match = re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})(?:-rc\.([1-9][0-9]{0,5}))?', value or '')
    if not match:
        raise ValueError('Bounded stable or rc release version required')
    return (*map(int, match.group(1, 2, 3)), int(match[4] is None), int(match[4] or 0))


def path_name(value, *, component=None):
    if component is not None and (type(component) is not str or component not in COMPONENTS):
        raise ValueError('Fixed runtime component required')
    if (type(value) is not str or not 1 <= len(value) <= 180
            or not re.fullmatch(r'[A-Za-z0-9_@.+/-]+', value)):
        raise ValueError('Portable relative code path required')
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or any(part in ('.', '..') for part in path.parts)
            or any(part in PRIVATE or part.startswith('.env') for part in path.parts)):
        raise ValueError('Private or non-code update path refused')
    script = value in RUNTIME_SCRIPTS and component in (None, RUNTIME_SCRIPTS[value])
    dependency = (len(path.parts) >= 3 and path.parts[0] == 'node_modules' and component != 'frontend'
                  and (path.name in DEPENDENCY_METADATA or path.name.endswith(('.d.ts', '.js.map'))
                       or len(path.parts) >= 4 and path.parts[-2:] == ('.github', 'FUNDING.yml')))
    if (path.suffix not in EXTENSIONS and path.name not in ('LICENSE', 'COPYING', 'NOTICE')
            and not script and not dependency):
        raise ValueError('Private or non-code update path refused')
    return value


def inventory(rows, *, component=None):
    if type(rows) is not list or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError('Bounded complete component inventory required')
    result = {}
    total = 0
    for row in rows:
        if (type(row) is not dict or set(row) != {'path', 'size', 'sha256', 'mode'}
                or type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE
                or type(row['mode']) is not int or row['mode'] not in (0o644, 0o755)):
            raise ValueError('Closed regular code-file record required')
        name = path_name(row['path'], component=component)
        if name in result:
            raise ValueError('Duplicate component path')
        digest(row['sha256'])
        result[name] = row
        total += row['size']
    if list(result) != sorted(result) or total > MAX_TOTAL:
        raise ValueError('Sorted bounded inventory required')
    return result


def canonical_archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:', format=tarfile.USTAR_FORMAT) as archive:
        for name, (raw, mode) in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.mode, item.size = mode, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return output.getvalue()


def review(raw, expected_sha256):
    expected_sha256 = digest(expected_sha256)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ARCHIVE or sha(raw) != expected_sha256:
        raise ValueError('Update archive does not match independently trusted digest')
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive:
            if (item.name in files or item.type != tarfile.REGTYPE or item.pax_headers
                    or item.sparse is not None or item.size < 0 or item.size > MAX_FILE
                    or item.mode not in (0o644, 0o755) or len(files) > MAX_FILES):
                raise ValueError('Unique bounded regular archive members required')
            if item.name != MANIFEST:
                parts = PurePosixPath(item.name).parts
                if (len(parts) < 4 or parts[:2] != ('droiddeck-update', 'payload')
                        or parts[2] not in COMPONENTS or str(PurePosixPath(item.name)) != item.name):
                    raise ValueError('Only fixed runtime component payloads allowed')
                path_name('/'.join(parts[3:]), component=parts[2])
            elif item.mode != 0o644 or item.size > 1024 * 1024:
                raise ValueError('Bounded manifest required')
            total += item.size
            if total > MAX_TOTAL:
                raise ValueError('Expanded update exceeds bound')
            payload = archive.extractfile(item).read(MAX_FILE + 1)
            if len(payload) != item.size:
                raise ValueError('Complete payload required')
            files[item.name] = payload, item.mode
    if MANIFEST not in files or canonical_archive(files) != raw:
        raise ValueError('Canonical closed update archive required; not a clean installer')
    manifest = document(files[MANIFEST][0])
    if (type(manifest) is not dict or set(manifest) != {'schema', 'kind', 'base', 'target', 'platform', 'dataPolicy', 'components'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['kind'] != 'droiddeck-runtime-update' or manifest['base'] != BASE
            or manifest['platform'] != PLATFORM or manifest['dataPolicy'] != 'preserve'):
        raise ValueError('Exact rc.2 base and runtime-only update policy required')
    target = manifest['target']
    if (type(target) is not dict or set(target) != {'version', 'sourceRevision', 'sourceSha256'}
            or type(target['version']) is not str or version(target['version']) <= version(BASE['version'])
            or type(target['sourceRevision']) is not str or not re.fullmatch('[a-f0-9]{40}', target['sourceRevision'])
            or digest(target['sourceSha256']) == BASE['sourceSha256']):
        raise ValueError('Strictly newer independently built target release required')
    components = manifest['components']
    if type(components) is not list or not 1 <= len(components) <= len(COMPONENTS):
        raise ValueError('Explicit component replacements required')
    names, required, summaries = [], {MANIFEST}, []
    count, expanded = 0, 0
    for component in components:
        if (type(component) is not dict or set(component) != {'name', 'before', 'after'} or type(component['name']) is not str
                or component['name'] not in COMPONENTS or component['name'] in names):
            raise ValueError('Unique fixed component name required')
        name = component['name']
        names.append(name)
        before, after = inventory(component['before'], component=name), inventory(component['after'], component=name)
        if before == after:
            raise ValueError('Unchanged component is not an update')
        for path, row in after.items():
            member = PREFIX + 'payload/' + name + '/' + path
            required.add(member)
            payload, mode = files.get(member, (None, None))
            if payload is None or (len(payload), sha(payload), mode) != (row['size'], row['sha256'], row['mode']):
                raise ValueError('Exact target component payload inventory required')
        count += len(after)
        expanded += sum(row['size'] for row in after.values())
        summaries.append({'component': name, 'targetRoot': COMPONENTS[name],
                          'added': len(after.keys() - before.keys()), 'removed': len(before.keys() - after.keys()),
                          'modified': sum(before[key] != after[key] for key in before.keys() & after.keys()),
                          'targetFiles': len(after)})
    if names != sorted(names) or required != set(files) or count > MAX_FILES or expanded > MAX_TOTAL:
        raise ValueError('Closed sorted component payload set required')
    return {'kind': 'runtime-update-review', 'schema': 1, 'bundleSha256': expected_sha256,
            'base': manifest['base'], 'target': target, 'components': summaries,
            'archiveDigestMatched': True, 'signatureVerified': False,
            'originalInstallationOwnershipVerified': False, 'installable': False,
            'restartRequiredForFutureApply': True, 'androidDataChangesAllowed': False,
            'tlsIdentityChangesAllowed': False, 'servicesChanged': False,
            'pendingGates': ['authenticated-release-trust', 'reproducible-target-artifacts',
                             'original-owned-base-admission', 'supervised-apply-and-rollback', 'disposable-vm-acceptance']}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, maximum=MAX_ARCHIVE, *, owner=None):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute file path required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise ValueError('Bounded single-link input file required')
        if owner is not None and (before.st_uid != owner or stat.S_IMODE(before.st_mode) != 0o600):
            raise ValueError('Private staged file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if (len(raw) != before.st_size or identity(os.fstat(fd)) != identity(before)
                or identity(path.lstat()) != identity(before)):
            raise ValueError('Input changed during reading')
        return raw
    finally:
        os.close(fd)


def unprivileged_linux():
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('Private staging requires an unprivileged Linux account')


def parent_directory(stack, path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute private staging path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    stack.callback(os.close, fd)
    held = []
    for part in path.parts[1:]:
        parent = fd
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ValueError('Non-writable root/account-owned ancestor required')
        held.append((parent, part, fd, info.st_dev, info.st_ino))
    leaf = os.fstat(fd)
    if leaf.st_uid != os.geteuid() or stat.S_IMODE(leaf.st_mode) != 0o700:
        raise ValueError('Account-owned mode0700 staging parent required')

    def check():
        for parent, part, handle, dev, ino in held:
            for info in (os.fstat(handle), os.stat(part, dir_fd=parent, follow_symlinks=False)):
                if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (dev, ino)
                        or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
                    raise ValueError('Staging directory generation changed')
        current = os.fstat(fd)
        if current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o700:
            raise ValueError('Private staging parent changed')
    check()
    return fd, check


def write_new(fd, name, raw):
    leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(leaf, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(leaf)
    finally:
        os.close(leaf)
    os.fsync(fd)


def stage(archive, expected_sha256, output, *, after_write=None):
    unprivileged_linux()
    raw = read_file(archive)
    report = review(raw, expected_sha256)
    output = Path(output)
    if output.name in ('', '.', '..'):
        raise ValueError('New private staging directory required')
    with ExitStack() as stack:
        parent, check = parent_directory(stack, output.parent)
        os.mkdir(output.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        owner = os.fstat(fd)
        if stat.S_IMODE(owner.st_mode) != 0o700:
            raise ValueError('Private stage mode required')

        def owned_stage():
            check()
            for info in (os.fstat(fd), os.stat(output.name, dir_fd=parent, follow_symlinks=False)):
                if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (owner.st_dev, owner.st_ino)
                        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
                    raise ValueError('Original private staging directory required')
        intent = {'schema': 1, 'kind': 'runtime-update-stage', 'bundleSha256': expected_sha256,
                  'device': owner.st_dev, 'inode': owner.st_ino, 'uid': os.geteuid()}
        for name, data in (
                ('intent.json', encode(intent)),
                ('bundle.tar', raw), ('review.json', encode(report)),
                ('complete.json', encode({'schema': 1, 'bundleSha256': expected_sha256,
                                         'intentSha256': sha(encode(intent)), 'reviewSha256': sha(encode(report))}))):
            owned_stage()
            write_new(fd, name, data)
            if after_write:
                after_write(name)
        owned_stage()
    return inspect_stage(output, expected_sha256)


def inspect_stage(directory, expected_sha256):
    unprivileged_linux()
    directory = Path(directory)
    with ExitStack() as stack:
        fd, check = parent_directory(stack, directory)
        if set(os.listdir(fd)) != {'intent.json', 'bundle.tar', 'review.json', 'complete.json'}:
            raise ValueError('Completed original stage required; preserve incomplete directory')
        values, identities = {}, {}
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                    or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Private single-link staged files required')
            identities[name] = identity(info)
            values[name] = read_file(directory / name, MAX_ARCHIVE if name == 'bundle.tar' else 1024 * 1024,
                                     owner=os.geteuid())
        report = review(values['bundle.tar'], expected_sha256)
        owner = os.fstat(fd)
        intent = {'schema': 1, 'kind': 'runtime-update-stage', 'bundleSha256': expected_sha256,
                  'device': owner.st_dev, 'inode': owner.st_ino, 'uid': os.geteuid()}
        if (document(values['intent.json']) != intent
                or values['review.json'] != encode(report)
                or document(values['complete.json']) != {'schema': 1, 'bundleSha256': expected_sha256,
                                                        'intentSha256': sha(encode(intent)), 'reviewSha256': sha(encode(report))}):
            raise ValueError('Stage receipt or payload changed')
        if (set(os.listdir(fd)) != set(identities) or any(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != original
                                                        for name, original in identities.items())):
            raise ValueError('Staged files changed during inspection')
        check()
    return {**report, 'staged': True, 'payloadExecuted': False}


def main(args):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('review', 'stage', 'inspect-stage'))
    parser.add_argument('input', type=Path)
    parser.add_argument('--trusted-sha256', required=True)
    parser.add_argument('--new-directory', type=Path)
    value = parser.parse_args(args)
    if (value.operation == 'stage') != (value.new_directory is not None):
        raise ValueError('Only stage accepts a new output directory')
    if value.operation == 'review':
        result = review(read_file(value.input), value.trusted_sha256)
    elif value.operation == 'stage':
        result = stage(value.input, value.trusted_sha256, value.new_directory)
    else:
        result = inspect_stage(value.input, value.trusted_sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Runtime update preparation refused; preserve partial stages: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
