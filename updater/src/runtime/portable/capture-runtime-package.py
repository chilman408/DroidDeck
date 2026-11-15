# SPDX-License-Identifier: GPL-3.0-only
"""Build/review a complete capture worker tree from Git and pinned dependencies.

Offline preparation only: no extraction, signing, activation or installed-tree input.
This distinct candidate domain never changes the original 76/77-file recipes or
claims the old gateway-only signed updater can install portable/config changes.
"""
import importlib.util
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
import tarfile


def load(name):
    spec = importlib.util.spec_from_file_location('capture_package_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


T = load('update-target')
P, D = T.P, T.D
GROUPS = {
    'browser-connection': 'helper policy proxy-store socket-startup tls-import',
    'portable': 'configuration-generation desktop-preflight gpu-access protected-config read-bounded runtime-account runtime-config worker-config worker-entry capture-policy',
    'waydroid-display': 'compositor-source frame-check gps-helper gps-lifecycle gps-protocol gps-store graphics-restart headless-session-preflight helper host-adapter location-adapter pid-compat pid-compat-hook probe runtime-commands runtime-discovery runtime-lock runtime-loop runtime-main runtime-native runtime-ownership runtime-preflight runtime-readiness runtime-supervisor session-lifecycle session-preflight session-probe session-recovery store touch-adapter touch-geometry transaction',
}
SOURCES = tuple(sorted([
    *(directory + '/' + name + '.js' for directory, names in GROUPS.items() for name in names.split()),
    *('preview-gateway/' + name for name in T.GATEWAY),
    'waydroid-display/native-stop.py', 'waydroid-display/logical-screencap.sh',
    'waydroid-display/package.json', 'waydroid-display/package-lock.json',
    'portable/capture-policy-controller.py', 'portable/capture-policy-transaction.py',
    'portable/capture-video-recycle.py', 'portable/capture-release-authority.py',
    'portable/capture-release-verifier.json', 'preview-gateway/droiddeck-logo.png',
]))
SOURCE_PATHS = {name: 'runtime/' + name for name in SOURCES}
SOURCE_PATHS['preview-gateway/droiddeck-logo.png'] = 'runtime/branding/droiddeck-logo.png'
PREFIX = 'droiddeck-capture-runtime/'
MANIFEST = PREFIX + 'manifest.json'
KIND = 'droiddeck-capture-runtime-candidate'
CONTRACT = {
    'originalRuntimeAndConfig': 'preserve-sealed-namespace',
    'runtimeView': 'complete-service-private-tree',
    'configurationView': 'independently-owned-service-private-tree',
    'policyDefault': 'cpu', 'androidData': 'preserve', 'nativeArtifacts': 'retain-and-independently-admit',
    'activationAuthority': 'not-implemented',
}
MAX_FILE, MAX_TOTAL, MAX_ARCHIVE = 8 * 1024**2, 32 * 1024**2, 40 * 1024**2
CATALOG_PIN = '858ccbef278c91fc8a139161f2572518168c7907871adb78f2e783b0a8050fdf'


def selected_source(repo, revision):
    """Read only explicitly selected Git blobs, never the working/installed tree."""
    if type(revision) is not str or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Exact original Git commit required')
    commit = T.git(repo, ['cat-file', 'commit', revision])
    import hashlib
    if hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest() != revision:
        raise ValueError('Original commit object required')
    requested = list(SOURCE_PATHS.values())
    objects = {}
    for raw in T.git(repo, ['ls-tree', '-rz', '--long', revision, '--', *requested]).split(b'\0'):
        if not raw:
            continue
        match = re.fullmatch(rb'(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t(.+)', raw)
        if not match:
            raise ValueError('Regular canonical source objects required')
        _mode, obj, size, name = match.groups()
        name = name.decode('ascii')
        if name in objects or name not in requested or not 0 < int(size) <= MAX_FILE:
            raise ValueError('Closed bounded selected source objects required')
        objects[name] = obj, int(size)
    if set(objects) != set(requested):
        raise ValueError('Complete selected runtime source required')
    result = {}
    for target, name in SOURCE_PATHS.items():
        obj, size = objects[name]
        raw = T.git(repo, ['cat-file', 'blob', obj.decode('ascii')])
        if len(raw) != size or hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + raw).hexdigest().encode() != obj:
            raise ValueError('Original selected blob bytes required')
        result[target] = raw
    validate_source(result)
    return result


def validate_source(files):
    if set(files) != set(SOURCES):
        raise ValueError('Closed complete worker source set required')
    for policy in D.POLICIES.values():
        for name, key in (('package.json', 'runtimeManifestSha256'), ('package-lock.json', 'runtimeLockSha256')):
            if P.sha(files[policy['directory'] + '/' + name]) != policy[key]:
                raise ValueError('Reviewed runtime package/lock pair required')
    if P.sha(files['portable/capture-release-verifier.json']) != CATALOG_PIN:
        raise ValueError('Original release-verifier catalog required')
    artwork = files['preview-gateway/droiddeck-logo.png']
    if len(artwork) != 664836 or P.sha(artwork) != 'd50a79c92129aa9e6462eedb0294ce0b5c09da1b91433672225f31c5f938e475':
        raise ValueError('Fixed approved artwork source required')
    version = P.document(files['preview-gateway/release-version.json'])
    if type(version) is not dict or set(version) != {'version'}:
        raise ValueError('Closed runtime health version required')
    P.version(version['version'])
    # Local literal imports must all resolve inside this COMPLETE tree. Dynamic
    # worker dispatch is covered by the fixed worker-entry/source inventory.
    for name, raw in files.items():
        if not name.endswith('.js'):
            continue
        for match in re.finditer(rb"\brequire\(\s*['\"](\.[^'\"]+)['\"]\s*\)", raw):
            target = posixpath.normpath(str(PurePosixPath(name).parent / match[1].decode('ascii')))
            if target not in files and target + '.js' not in files and target + '.json' not in files:
                raise ValueError('Missing local runtime dependency: ' + name + ' -> ' + target)
    return version['version']


def read_input(path, maximum):
    """Public pinned builder inputs; preserve strict Linux generation checking.

    Python 3.14 Windows fstat/Win32 pathname ctime may differ for the SAME file.
    Keep full before/after descriptor identity; only that cross-API ctime field
    is omitted on Windows pathname comparison. Device/inode/mode/links/size/mtime
    and cryptographic publisher pins remain mandatory. No installed ownership
    authority or privileged extraction uses this offline builder helper.
    """
    path = Path(os.path.abspath(path))
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise ValueError('Bounded public single-link input required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        original, held, named = P.identity(before), P.identity(os.fstat(fd)), P.identity(path.lstat())
        if (len(raw) != before.st_size or held != original
                or (named[:-1] if sys.platform == 'win32' else named) != (original[:-1] if sys.platform == 'win32' else original)):
            raise ValueError('Public builder input generation changed')
        return raw
    finally:
        os.close(fd)


def dependency_files(role, root):
    root = Path(os.path.abspath(root))
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('Real publisher dependency root required')
    manifest = read_input(root / 'bundle-manifest.json', 1048576)
    records = D.dependency_records(role, manifest)
    observed, directories = set(), set()
    for name in records:
        directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            path = Path(current) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                if relative not in directories:
                    raise ValueError('Unexpected publisher dependency directory')
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                observed.add(relative)
            else:
                raise ValueError('No publisher links, special files or hardlinks')
    if observed != {*records, 'bundle-manifest.json'}:
        raise ValueError('Exact publisher dependency inventory required')
    result = {}
    for name, (size, digest) in records.items():
        raw = read_input(root / name, MAX_FILE)
        if (len(raw), P.sha(raw)) != (size, digest):
            raise ValueError('Pinned publisher bytes required')
        result[D.POLICIES[role]['directory'] + '/node_modules/' + name] = raw
    return manifest, result


def records(files):
    return [{'path': name, 'size': len(raw), 'sha256': P.sha(raw)} for name, raw in sorted(files.items())]


def make_archive(revision, source, dependencies):
    version = validate_source(source)
    if type(revision) is not str or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Exact source revision required')
    if set(dependencies) != set(D.POLICIES):
        raise ValueError('Both pinned publisher dependency roles required')
    files = dict(source)
    manifests = {}
    for role, (raw, payload) in dependencies.items():
        pinned = D.dependency_records(role, raw)
        expected = {D.POLICIES[role]['directory'] + '/node_modules/' + name: pin for name, pin in pinned.items()}
        if set(payload) != set(expected) or any((len(payload[name]), P.sha(payload[name])) != pin for name, pin in expected.items()):
            raise ValueError('Complete pinned dependency payload required')
        files.update(payload)
        manifests[PREFIX + 'dependencies/' + role + '.json'] = (raw, 0o644)
    if sum(map(len, files.values())) > MAX_TOTAL:
        raise ValueError('Bounded full runtime payload required')
    manifest = {'schema': 1, 'kind': KIND, 'sourceRevision': revision, 'runtimeVersion': version,
        'sourceFilesSha256': P.sha(P.encode(records(source))),
        'platform': P.PLATFORM, 'contract': CONTRACT, 'installable': False,
        'dependencies': {role: D.POLICIES[role]['bundle'] for role in sorted(dependencies)}, 'files': records(files)}
    raw = P.canonical_archive({MANIFEST: (P.encode(manifest), 0o644), **manifests,
        **{PREFIX + 'runtime/' + name: (value, 0o644) for name, value in files.items()}})
    return raw, review(raw, P.sha(raw))


def review(raw, expected):
    P.digest(expected)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ARCHIVE or P.sha(raw) != expected:
        raise ValueError('Exact independently supplied candidate digest required')
    members, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for item in archive:
            if (len(members) >= 1024 or item.name in members or item.type != tarfile.REGTYPE
                    or item.mode != 0o644 or item.pax_headers or item.sparse is not None
                    or not 0 <= item.size <= MAX_FILE or item.name.startswith('/') or '..' in item.name.split('/')):
                raise ValueError('Closed canonical regular candidate members required')
            total += item.size
            if total > MAX_TOTAL + 1048576:
                raise ValueError('Bounded candidate payload required')
            content = archive.extractfile(item).read(MAX_FILE + 1)
            if len(content) != item.size:
                raise ValueError('Complete candidate bytes required')
            members[item.name] = (content, item.mode)
    if P.canonical_archive(members) != raw:
        raise ValueError('Canonical archive bytes required')
    manifest = P.document(members[MANIFEST][0])
    keys = {'schema', 'kind', 'sourceRevision', 'runtimeVersion', 'sourceFilesSha256', 'platform', 'contract', 'installable', 'dependencies', 'files'}
    if (type(manifest) is not dict or set(manifest) != keys or type(manifest['schema']) is not int
            or manifest['schema'] != 1 or manifest['kind'] != KIND or manifest['installable'] is not False
            or P.encode(manifest['contract']) != P.encode(CONTRACT) or manifest['platform'] != P.PLATFORM
            or type(manifest['sourceRevision']) is not str or not re.fullmatch('[a-f0-9]{40}', manifest['sourceRevision'])
            or manifest['dependencies'] != {role: D.POLICIES[role]['bundle'] for role in sorted(D.POLICIES)}
            or type(manifest['files']) is not list or len(manifest['files']) > 900):
        raise ValueError('Closed separate capture candidate contract required')
    files = {}
    for row in manifest['files']:
        if (type(row) is not dict or set(row) != {'path', 'size', 'sha256'} or type(row['path']) is not str
                or row['path'] in files or type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE):
            raise ValueError('Closed bounded candidate file record required')
        P.digest(row['sha256'])
        name = PREFIX + 'runtime/' + row['path']
        content = members[name][0]
        if (len(content), P.sha(content)) != (row['size'], row['sha256']):
            raise ValueError('Exact candidate payload required')
        files[row['path']] = content
    if list(files) != sorted(files):
        raise ValueError('Sorted candidate inventory required')
    source = {name: files[name] for name in SOURCES}
    if validate_source(source) != manifest['runtimeVersion'] or P.sha(P.encode(records(source))) != manifest['sourceFilesSha256']:
        raise ValueError('Exact source and health binding required')
    expected_files = set(SOURCES)
    for role in D.POLICIES:
        pin = D.dependency_records(role, members[PREFIX + 'dependencies/' + role + '.json'][0])
        for name, (size, digest) in pin.items():
            target = D.POLICIES[role]['directory'] + '/node_modules/' + name
            expected_files.add(target)
            if (len(files[target]), P.sha(files[target])) != (size, digest):
                raise ValueError('Independent publisher payload pin required')
    if set(files) != expected_files or set(members) != {MANIFEST,
            *(PREFIX + 'dependencies/' + role + '.json' for role in D.POLICIES),
            *(PREFIX + 'runtime/' + name for name in expected_files)}:
        raise ValueError('No extra or missing full runtime files permitted')
    return {'kind': KIND, 'bundleSha256': expected, 'manifestSha256': P.sha(members[MANIFEST][0]),
        'sourceRevision': manifest['sourceRevision'], 'runtimeVersion': manifest['runtimeVersion'],
        'sourceFiles': len(SOURCES), 'dependencyFiles': len(files) - len(SOURCES), 'files': len(files),
        'signed': False, 'installable': False, 'readyForActivation': False,
        'payloadExecuted': False, 'servicesChanged': False}


def main(args):
    if len(args) == 6 and args[0] == 'build':
        source = selected_source(args[1], args[2])
        deps = {role: dependency_files(role, root) for role, root in zip(('gateway', 'display'), args[3:5])}
        raw, result = make_archive(args[2], source, deps)
        output = Path(args[5]); output.mkdir(mode=0o700)
        T.write_new(output / 'capture-runtime.tar', raw)
        T.write_new(output / 'build-receipt.json', P.encode(result))
    elif len(args) == 3 and args[0] == 'review':
        result = review(read_input(args[1], MAX_ARCHIVE), args[2])
    else:
        raise ValueError('build REPO COMMIT GATEWAY_DEPS DISPLAY_DEPS NEW_OUTPUT or review ARCHIVE TRUSTED_SHA256')
    print(P.encode(result).decode('ascii').strip())


if __name__ == '__main__':
    main(sys.argv[1:])
