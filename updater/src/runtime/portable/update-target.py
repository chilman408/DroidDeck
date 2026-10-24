# SPDX-License-Identifier: GPL-3.0-only
"""Reproduce a gateway-only update from committed source and pinned dependencies.

No installed code is used as a target. No dependency scripts, payloads, services,
signers or privileged operations are executed. Admission/apply remain separate.
"""
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


def load(name):
    spec = importlib.util.spec_from_file_location('target_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


P = load('update-package')
D = load('runtime-candidate-dependencies')
VERSION = '0.1.1-rc.5'
PREFIX = 'droiddeck-update-source/'
LIMIT = 32 * 1024 * 1024
SOURCE_PREFIXES = ('runtime/', 'tools/', 'config/', 'tests/')
SOURCE_SUFFIXES = {'.js', '.json', '.py', '.ps1', '.patch', '.sh', '.service', '.conf', '.md', '.txt',
                   '.java', '.c', '.cpp', '.h', '.bp', '.xml', '.axml', '.pam', '.example', '.svg', '.png', '.pl', '.swap'}
GATEWAY = tuple(sorted((name + '.js' for name in (
    'audio-gain audio-launcher audio-relay audio-settings audio-stream browser-access capture-layout capture-lease '
    'browser-renderer-policy compositor-video-launcher device-info display-control display-helper-client gps-control gps-helper-client '
    'gps-location h264-relay rear-display-settings runtime-probe scrcpy-stream server video-capture-controller '
    'video-launcher video-settings').split()))) + ('compositor-h264.py', 'package.json', 'package-lock.json')
GATEWAY = tuple(sorted(GATEWAY))


def source_name(name):
    path = PurePosixPath(name)
    if (type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_./@+-]{1,200}', name)
            or not name.startswith(SOURCE_PREFIXES) or path.is_absolute() or str(path) != name
            or any(part in ('.', '..', '.git', '.ssh', '.config') for part in path.parts)
            or path.suffix not in SOURCE_SUFFIXES and path.name not in ('.gitignore', 'Containerfile')):
        raise ValueError('Closed public source path required')
    return name


def git(repo, args, data=None):
    result = subprocess.run(['git', '--no-pager', '--no-replace-objects', '--no-lazy-fetch',
        '-c', 'core.fsmonitor=false', '-C', str(Path(repo).resolve()), *args], input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'})
    if len(result.stdout) > LIMIT or len(result.stderr) > 65536:
        raise ValueError('Bounded original Git output required')
    return result.stdout


def source_archive(repo, revision):
    if type(revision) is not str or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Exact reviewed commit required')
    commit = git(repo, ['cat-file', 'commit', revision])
    if hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest() != revision:
        raise ValueError('Original commit object required')
    rows = []
    for raw in git(repo, ['ls-tree', '-rz', '--long', revision, '--', 'runtime', 'tools', 'config', 'tests']).split(b'\0'):
        if not raw: continue
        match = re.fullmatch(rb'(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t(.+)', raw)
        if not match: raise ValueError('Regular committed sources only')
        _mode, obj, size, name = match.groups()
        name, size = source_name(name.decode('ascii')), int(size)
        if not 0 <= size <= P.MAX_FILE: raise ValueError('Bounded source blob required')
        rows.append((name, obj, size))
    if not 1 <= len(rows) <= 1800 or sum(row[2] for row in rows) > 24 * 1024 * 1024:
        raise ValueError('Bounded full source inventory required')
    batch = git(repo, ['cat-file', '--batch'], b''.join(obj + b'\n' for _, obj, _ in rows))
    files, offset = {}, 0
    for name, obj, size in rows:
        header = obj + b' blob ' + str(size).encode() + b'\n'
        if batch[offset:offset + len(header)] != header: raise ValueError('Original Git blob header required')
        offset += len(header)
        data = batch[offset:offset + size]; offset += size
        if (batch[offset:offset + 1] != b'\n'
                or hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + data).hexdigest().encode() != obj):
            raise ValueError('Original Git blob bytes required')
        offset += 1
        files[name] = data
    if offset != len(batch): raise ValueError('Unexpected extra source bytes')
    manifest = P.encode({'schema': 1, 'kind': 'droiddeck-runtime-update-source', 'sourceRevision': revision,
        'version': VERSION, 'installerIncluded': False,
        'files': [{'path': name, 'size': len(raw), 'sha256': P.sha(raw)} for name, raw in sorted(files.items())]})
    return P.canonical_archive({PREFIX + 'source-manifest.json': (manifest, 0o644),
                                **{PREFIX + 'src/' + name: (raw, 0o644) for name, raw in files.items()}})


def sources(raw):
    if not 0 < len(raw) <= LIMIT: raise ValueError('Bounded source archive required')
    members = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive:
            if (item.name in members or item.type != tarfile.REGTYPE or item.pax_headers
                    or item.mode != 0o644 or item.sparse is not None or not 0 <= item.size <= P.MAX_FILE):
                raise ValueError('Only canonical regular public source members allowed')
            content = archive.extractfile(item).read(P.MAX_FILE + 1)
            if len(content) != item.size: raise ValueError('Complete source bytes required')
            members[item.name] = (content, item.mode)
    if len(members) > 1801 or P.canonical_archive(members) != raw:
        raise ValueError('Canonical bounded source archive required')
    manifest = P.document(members[PREFIX + 'source-manifest.json'][0])
    if (type(manifest) is not dict or set(manifest) != {'schema', 'kind', 'sourceRevision', 'version', 'installerIncluded', 'files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['kind'] != 'droiddeck-runtime-update-source' or manifest['version'] != VERSION
            or manifest['installerIncluded'] is not False or type(manifest['sourceRevision']) is not str
            or not re.fullmatch('[a-f0-9]{40}', manifest['sourceRevision'])):
        raise ValueError('Closed update-only source manifest required')
    files = {}
    for row in manifest['files']:
        if type(row) is not dict or set(row) != {'path', 'size', 'sha256'} or type(row['size']) is not int:
            raise ValueError('Closed original source row required')
        name = source_name(row['path'])
        content = members[PREFIX + 'src/' + name][0]
        if name in files or (len(content), P.sha(content)) != (row['size'], row['sha256']):
            raise ValueError('Exact original source inventory required')
        files[name] = content
    if (list(files) != sorted(files) or set(members) != {PREFIX + 'source-manifest.json',
            *(PREFIX + 'src/' + name for name in files)}):
        raise ValueError('Sorted closed source set required')
    return manifest, files


def dependency_files(directory):
    directory = Path(directory)
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError('Real dependency bundle root required')
    raw = P.read_file(directory / 'bundle-manifest.json', 1024 * 1024)
    records = D.dependency_records('gateway', raw)
    files = {}
    observed = set()
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in (*dirs, *names):
            path = Path(current) / name
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('No dependency links or devices allowed')
            if stat.S_ISREG(info.st_mode): observed.add(path.relative_to(directory).as_posix())
    if observed != {*records, 'bundle-manifest.json'}:
        raise ValueError('Exact pinned publisher dependency inventory required')
    for name, (size, digest) in records.items():
        content = P.read_file(directory / name, P.MAX_FILE)
        if (len(content), P.sha(content)) != (size, digest):
            raise ValueError('Original publisher dependency bytes required')
        files['node_modules/' + name] = content
    return files


def build(source, inventory, dependencies):
    source_manifest, source_files = sources(source)
    value = P.document(inventory)
    if (type(value) is not dict or set(value) != {'schema', 'kind', 'base', 'components'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'original-runtime-code-inventory' or value['base'] != P.BASE
            or type(value['components']) is not list or [row['name'] for row in value['components']] != sorted(P.COMPONENTS)):
        raise ValueError('Exact full original public code inventory required')
    for row in value['components']:
        if set(row) != {'name', 'root', 'files'} or row['root'] != P.COMPONENTS[row['name']]:
            raise ValueError('Fixed original component roots required')
        P.inventory(row['files'], component=row['name'])
    before = next(row['files'] for row in value['components'] if row['name'] == 'gateway')
    payloads = {name: source_files['runtime/preview-gateway/' + name] for name in GATEWAY}
    payloads.update(dependency_files(dependencies))
    after = [{'path': name, 'size': len(data), 'sha256': P.sha(data), 'mode': 0o644}
             for name, data in sorted(payloads.items())]
    manifest = {'schema': 1, 'kind': 'droiddeck-runtime-update', 'base': P.BASE,
        'target': {'version': VERSION, 'sourceRevision': source_manifest['sourceRevision'], 'sourceSha256': P.sha(source)},
        'platform': P.PLATFORM, 'dataPolicy': 'preserve',
        'components': [{'name': 'gateway', 'before': before, 'after': after}]}
    archive = P.canonical_archive({P.MANIFEST: (P.encode(manifest), 0o644),
        **{P.PREFIX + 'payload/gateway/' + name: (content, 0o644) for name, content in payloads.items()}})
    review = P.review(archive, P.sha(archive))
    return archive, {**review, 'sourceArchiveSha256': P.sha(source), 'publisherDependencyFiles': len(payloads) - len(GATEWAY),
                     'payloadExecuted': False, 'nativeArtifactsChanged': False, 'signed': False, 'readyForApply': False}


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream: stream.write(data); stream.flush(); os.fsync(fd)
    finally: os.close(fd)


def main(args):
    if len(args) == 4 and args[0] == 'source':
        data = source_archive(args[1], args[2]); write_new(Path(args[3]), data)
        result = {'kind': 'droiddeck-runtime-update-source', 'sha256': P.sha(data), 'installable': False}
    elif len(args) == 5 and args[0] == 'build':
        archive, result = build(P.read_file(Path(args[1]), LIMIT), P.read_file(Path(args[2]), 1048576), Path(args[3]))
        output = Path(args[4]); output.mkdir(mode=0o700)
        write_new(output / 'update.tar', archive); write_new(output / 'build-receipt.json', P.encode(result))
    else: raise ValueError('source REPO COMMIT NEW_ARCHIVE or build SOURCE INVENTORY PINNED_DEPENDENCIES NEW_OUTPUT')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main(sys.argv[1:])
