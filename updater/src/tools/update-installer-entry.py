# SPDX-License-Identifier: GPL-3.0-only
"""Self-contained pilot updater launcher. Verify signature before importing code.

The .pyz is downloaded as a file and checked against the published SHA256. Its
signed statement authenticates both payload and source archive. No network access,
downloaded hooks, Android-data extraction or private-key access occurs here.
"""
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

PUBLIC_KEY = '302a300506032b65700321009d63ba428047b862b5cd1fb5ac994814d985b9a015ecf5805de1261d7825e69c'
KEY_PIN = '4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f'
PREFIX = 'droiddeck-update-source/'
ENTRIES = {'__main__.py', 'source.tar', 'update.tar', 'statement.json', 'signature.bin'}
LIMIT = 32 * 1024 * 1024
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def encode(value): return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value: raise ValueError('Duplicate JSON key refused')
        value[name] = item
    return value


def document(raw):
    value = json.loads(raw, object_pairs_hook=unique)
    if encode(value) != raw: raise ValueError('Canonical published JSON required')
    return value


def verify_signature(statement, signature):
    if not 1 <= len(statement) <= 8192 or len(signature) != 64: raise ValueError('Bounded Ed25519 statement/signature required')
    key = bytes.fromhex(PUBLIC_KEY)
    if sha(key) != KEY_PIN: raise ValueError('Original public signing key fingerprint required')
    with ExitStack() as stack:
        handles = []
        for raw in (key, statement, signature):
            file = stack.enter_context(tempfile.TemporaryFile()); file.write(raw); file.flush(); file.seek(0); handles.append(file.fileno())
        names = ['/proc/self/fd/' + str(fd) for fd in handles]
        result = subprocess.run(['/usr/bin/openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
            '-inkey', names[0], '-rawin', '-in', names[1], '-sigfile', names[2]], pass_fds=tuple(handles),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='/', env=ENV, timeout=15)
        if result.returncode: raise ValueError('Release signature did not verify against the published pilot key')


def source_name(name):
    if type(name) is not str: raise ValueError('String public source name required')
    path = PurePosixPath(name)
    if (not re.fullmatch('[A-Za-z0-9_./@+-]{1,200}', name) or path.is_absolute() or str(path) != name
            or not name.startswith(('runtime/', 'tools/', 'config/', 'tests/'))
            or any(part in ('.', '..', '.git', '.ssh', '.config', '.env') for part in path.parts)
            or path.name in ('key.pem', 'cert.pem', 'credentials.json', 'id_rsa', 'id_ed25519')):
        raise ValueError('Closed public source name required')
    return name


def sources(raw, revision, version):
    if not 1 <= len(raw) <= LIMIT: raise ValueError('Bounded signed source archive required')
    members = {}; total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive:
            if (item.name in members or item.type != tarfile.REGTYPE or item.pax_headers or item.sparse is not None
                    or item.mode != 0o644 or not 0 <= item.size <= 1024 * 1024 or len(members) >= 1801):
                raise ValueError('Unique bounded regular source members required')
            if item.name != PREFIX + 'source-manifest.json':
                if not item.name.startswith(PREFIX + 'src/'): raise ValueError('Only public committed sources allowed')
                source_name(item.name[len(PREFIX + 'src/'):])
            total += item.size
            if total > 24 * 1024 * 1024: raise ValueError('Expanded source bound exceeded')
            data = archive.extractfile(item).read(1024 * 1024 + 1)
            if len(data) != item.size: raise ValueError('Complete source member required')
            members[item.name] = data
    manifest = document(members[PREFIX + 'source-manifest.json'])
    if (set(manifest) != {'schema', 'kind', 'sourceRevision', 'version', 'installerIncluded', 'files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['kind'] != 'droiddeck-runtime-update-source' or manifest['installerIncluded'] is not False
            or manifest['sourceRevision'] != revision or manifest['version'] != version
            or type(manifest['files']) is not list or not 1 <= len(manifest['files']) <= 1800):
        raise ValueError('Exact signed update-only source manifest required')
    files = {}
    for row in manifest['files']:
        if set(row) != {'path', 'size', 'sha256'} or type(row['size']) is not int: raise ValueError('Closed source row required')
        name = source_name(row['path']); data = members[PREFIX + 'src/' + name]
        if name in files or (len(data), sha(data)) != (row['size'], row['sha256']): raise ValueError('Exact committed source bytes required')
        files[name] = data
    if list(files) != sorted(files) or set(members) != {PREFIX + 'source-manifest.json', *(PREFIX + 'src/' + n for n in files)}:
        raise ValueError('Sorted closed source archive required')
    return manifest, files


def authenticate(entries):
    # Signature first: no bundled Python is imported or materialized before this.
    verify_signature(entries['statement.json'], entries['signature.bin'])
    value = document(entries['statement.json'])
    if (set(value) != {'schema', 'kind', 'channel', 'keySha256', 'bundleSha256', 'manifestSha256', 'base', 'target', 'platform', 'dataPolicy'}
            or type(value['schema']) is not int or value['schema'] != 1 or value['kind'] != 'droiddeck-runtime-release'
            or value['channel'] != 'pilot' or value['keySha256'] != KEY_PIN or value['dataPolicy'] != 'preserve'
            or sha(entries['update.tar']) != value['bundleSha256']
            or sha(entries['source.tar']) != value['target']['sourceSha256']):
        raise ValueError('Original signed pilot source/payload identity required')
    manifest, files = sources(entries['source.tar'], value['target']['sourceRevision'], value['target']['version'])
    return value, manifest, files


def protected_directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('Absolute protected directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022: raise ValueError('Protected root-owned ancestors required')
        return fd
    except BaseException: os.close(fd); raise


def generation(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def read(path, maximum=LIMIT):
    parent = protected_directory(path.parent)
    try: fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally: os.close(parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink, stat.S_IMODE(info.st_mode)) != (0, 0, 1, 0o600)
                or not 0 <= info.st_size <= maximum): raise ValueError('Bounded single-link protected file required')
        with os.fdopen(os.dup(fd), 'rb') as stream: raw = stream.read(maximum + 1)
        if len(raw) != info.st_size or generation(info) != generation(os.fstat(fd)) or generation(info) != generation(path.lstat()):
            raise ValueError('Original protected file changed while reading')
        return raw, generation(info)
    finally: os.close(fd)


def write(path, raw):
    parent = protected_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), 'wb') as stream: stream.write(raw); stream.flush()
            os.fsync(fd)
        finally: os.close(fd)
        os.fsync(parent)
    finally: os.close(parent)


def materialize(root, files):
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or str(path) != name or any(x in ('.', '..') for x in path.parts): raise ValueError('Fixed package member required')
    expected = {name: {'size': len(raw), 'sha256': sha(raw)} for name, raw in sorted(files.items())}
    expected_dirs = {'.', *(parent.as_posix() for name in files for parent in PurePosixPath(name).parents)}
    exists = os.path.lexists(root)
    if not exists:
        parent = protected_directory(root.parent)
        try: os.mkdir(root.name, mode=0o700, dir_fd=parent); os.fsync(parent)
        finally: os.close(parent)
    fd = protected_directory(root)
    try:
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != 0o700: raise ValueError('Original private package directory required')
        intent = encode({'schema': 1, 'rootIdentity': [info.st_dev, info.st_ino], 'files': expected})
        def directory_identities():
            directories = {}
            for current, children, _names in os.walk(root, followlinks=False):
                path = Path(current); descriptor = protected_directory(path)
                try:
                    state = os.fstat(descriptor)
                    directories[path.relative_to(root).as_posix()] = [state.st_dev,state.st_ino,state.st_mode,state.st_uid,state.st_gid]
                finally: os.close(descriptor)
                for child in children:
                    if (path / child).is_symlink(): raise ValueError('Linked staging directory refused')
            if set(directories) != expected_dirs: raise ValueError('Closed original staging directory namespace required')
            return directories

        if exists:
            if read(root / 'intent.json')[0] != intent: raise ValueError('Original immutable package receipt required')
            completion = document(read(root / 'complete.json')[0])
        else:
            write(root / 'intent.json', intent)
            for name, raw in sorted(files.items()):
                path = root / name
                current = root
                for part in Path(name).parent.parts:
                    parent = protected_directory(current)
                    try:
                        try: os.mkdir(part, mode=0o700, dir_fd=parent); os.fsync(parent)
                        except FileExistsError: pass
                    finally: os.close(parent)
                    current /= part
                write(path, raw)
            completion = {'schema': 1, 'intentSha256': sha(intent), 'generations': {name: read(root / name)[1] for name in sorted(files)},
                          'directories': directory_identities()}
            write(root / 'complete.json', encode(completion))
        actual = set()
        for current, dirs, names in os.walk(root, followlinks=False):
            for name in dirs:
                child = Path(current) / name; held = protected_directory(child); os.close(held)
            actual.update((Path(current) / name).relative_to(root).as_posix() for name in names)
        if actual != {*files, 'intent.json', 'complete.json'}: raise ValueError('Closed original package namespace required')
        verified = {'schema': 1, 'intentSha256': sha(intent), 'generations': {}, 'directories': directory_identities()}
        for name, raw in sorted(files.items()):
            content, gen = read(root / name)
            if content != raw: raise ValueError('Original signed package source bytes required')
            verified['generations'][name] = gen
        if completion != verified: raise ValueError('Original package file inodes/generations required')
        return root
    finally: os.close(fd)


def package_entries(path):
    with zipfile.ZipFile(path) as archive:
        rows = archive.infolist()
        if len(rows) != len(ENTRIES) or {row.filename for row in rows} != ENTRIES: raise ValueError('Closed standalone updater package required')
        if sum(row.file_size for row in rows) > 48 * 1024 * 1024: raise ValueError('Package size bound exceeded')
        if any(row.is_dir() or row.file_size > LIMIT or row.flag_bits & 1 for row in rows): raise ValueError('Bounded public package members required')
        return {row.filename: archive.read(row) for row in rows}


def main(args):
    sys.dont_write_bytecode = True
    if sys.platform != 'linux': raise ValueError('Ubuntu Linux updater required')
    entries = package_entries(Path(sys.argv[0])); release, _manifest, files = authenticate(entries)
    if args == ['verify']:
        print(json.dumps({'kind': 'droiddeck-signed-updater-verified', 'target': release['target'], 'keySha256': KEY_PIN, 'servicesChanged': False})); return
    if (len(args) not in (3, 4) or args[0] != 'install' or args[1] not in ('completed', 'recovered')
            or len(args) == 4 and args[3] != '--accept-existing-policy' or os.geteuid() != 0):
        raise ValueError('verify OR sudo python3 PACKAGE.pyz install completed|recovered ABSOLUTE_RC2_PACKAGE [--accept-existing-policy]')
    os.umask(0o077)
    root = Path('/usr/local/share') / ('droiddeck-update-' + release['target']['sourceSha256'][:16])
    materialize(root, {**{'src/' + name: raw for name, raw in files.items()},
        **{name: entries[name] for name in ('source.tar', 'update.tar', 'statement.json', 'signature.bin')}})
    source = root / 'src'; entry = source / 'runtime/portable/gateway-update-release.py'
    spec = importlib.util.spec_from_file_location('verified_pilot_updater', entry)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    # All code loaded here is authenticated by the exact signed source archive.
    module.B.A.authenticate(entries['update.tar'], entries['statement.json'], entries['signature.bin'],
        module.P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot', 'publicKeySpkiHex': PUBLIC_KEY}))
    pin = module.P.sha(module.P.encode(module.B.rows(module.B.overlays(source))))
    module.bootstrap(source, pin, args[1], args[2], accept_existing=len(args) == 4)
    enrolled = module.B.SOURCE / module.SELF
    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', str(enrolled), 'install', args[1], args[2],
        str(root / 'update.tar'), str(root / 'statement.json'), str(root / 'signature.bin')])


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except Exception as error:
        print('Updater refused: ' + str(error) + '. Preserve original records; do not replay incomplete enrollment.', file=sys.stderr)
        sys.exit(1)
