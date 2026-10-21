# SPDX-License-Identifier: GPL-3.0-only
"""Read-only installed-package metadata consistency, NOT publisher authenticity."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('host_support_namespace', Path(__file__).with_name('runtime-support-namespace.py'))
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)
A, S = N.A, N.S
FILES = MappingProxyType({
    '/usr/lib/systemd/system/waydroid-container.service': ('waydroid', 'lib/systemd/system/waydroid-container.service'),
    '/usr/lib/x86_64-linux-gnu/security/pam_unix.so': ('libpam-modules:amd64', 'usr/lib/x86_64-linux-gnu/security/pam_unix.so'),
    '/usr/lib/x86_64-linux-gnu/security/pam_systemd.so': ('libpam-systemd:amd64', 'usr/lib/x86_64-linux-gnu/security/pam_systemd.so'),
})
PACKAGES = ('libpam-modules:amd64', 'libpam-systemd:amd64', 'waydroid')
FORMAT = '${binary:Package}\t${Version}\t${Architecture}\t${Status}\n'
ALIAS = PurePosixPath('/lib')
MAX_QUERY, MAX_FILE = 4 * 1024 * 1024, 16 * 1024 * 1024


def _query(*args):
    result = subprocess.run(['/usr/bin/dpkg-query', *args], cwd='/',
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL,
        capture_output=True, check=True, timeout=5)
    if not 0 < len(result.stdout) <= MAX_QUERY or result.stderr:
        raise ValueError('Bounded successful installed package metadata required')
    return result.stdout


def _metadata():
    status = _query('-W', '--showformat=' + FORMAT, *PACKAGES)
    versions = {}
    for line in status.decode('ascii').splitlines():
        fields = line.split('\t')
        if len(fields) != 4:
            raise ValueError('Closed installed package status required')
        package, version, arch, state = fields
        if (package not in PACKAGES or package in versions or state != 'install ok installed'
                or arch != ('all' if package == 'waydroid' else 'amd64')
                or not re.fullmatch('[0-9A-Za-z.+:~_-]{1,128}', version)):
            raise ValueError('Exact installed host packages and architectures required')
        versions[package] = version
    if set(versions) != set(PACKAGES):
        raise ValueError('Complete installed host package status required')
    ownership = _query('-S', *('/' + recorded for _, recorded in FILES.values()))
    rows = ownership.decode('ascii').splitlines()
    expected = {package + ': /' + recorded for package, recorded in FILES.values()}
    if len(rows) != len(expected) or set(rows) != expected:
        raise ValueError('Exact package ownership without diversion required')
    pins, snapshots = {}, [status, ownership]
    for package in PACKAGES:
        raw = _query('--control-show', package, 'md5sums')
        snapshots.append(raw)
        records = {}
        lines = raw.decode('ascii').splitlines()
        if len(lines) > 8192:
            raise ValueError('Bounded installed package checksum records required')
        for line in lines:
            match = re.fullmatch(r'([a-f0-9]{32})  ([^\x00-\x20]+)', line)
            if match is None:
                raise ValueError('Canonical installed package checksum row required')
            pin, name = match.groups()
            path = PurePosixPath(name)
            if (len(name) > 1024 or '\\' in name or str(path) != name or path.is_absolute()
                    or '..' in path.parts or name in records):
                raise ValueError('Unique relative package checksum path required')
            records[name] = pin
        for path, (owner, name) in FILES.items():
            if owner == package:
                if name not in records:
                    raise ValueError('Owned host file missing from package checksum records')
                pins[path] = records[name]
    return tuple(snapshots), MappingProxyType(versions), MappingProxyType(pins)


def _budget():
    import resource
    directories = {p for name in FILES for p in PurePosixPath(name).parents}
    directories.update(ALIAS.parents)
    required = len(os.listdir('/proc/self/fd')) + len(directories) + len(FILES) + 1 + 4
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot hold host package files')
    return {'hostPackageDescriptorLimit': soft, 'hostPackageDescriptorUpperBound': required}


def hold_host_package_files(stack):
    A._root_linux()
    budget = _budget()
    metadata, versions, pins = _metadata()
    nodes, files = {}, {}

    def directory(path):
        if path in nodes:
            return nodes[path][0]
        parent = None if path == PurePosixPath('/') else directory(path.parent)
        name = '/' if parent is None else path.name
        before = S._directory(os.stat(name, dir_fd=parent, follow_symlinks=False), False)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if S._directory(os.fstat(fd), False) != before:
            raise ValueError('Original host package ancestor required')
        nodes[path] = fd, parent, name, before
        return fd

    def regular(entry):
        parent, name, fd, before, pair = entry
        for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                    or stat.S_IMODE(info.st_mode) != 0o644 or A._identity(info) != A._identity(before)):
                raise ValueError('Original root-owned single-link host package file required')

    alias_parent = directory(ALIAS.parent)
    alias_before = os.stat(ALIAS.name, dir_fd=alias_parent, follow_symlinks=False)
    alias_fd = os.open(ALIAS.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=alias_parent)
    stack.callback(os.close, alias_fd)

    def alias_check():
        for after_read in (False, True):
            if after_read and os.readlink(ALIAS.name, dir_fd=alias_parent) != 'usr/lib':
                raise ValueError('Reviewed merged-usr package path mapping required')
            for info in (os.fstat(alias_fd), os.stat(ALIAS.name, dir_fd=alias_parent, follow_symlinks=False)):
                if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                        or stat.S_IMODE(info.st_mode) != 0o777 or A._identity(info) != A._identity(alias_before)):
                    raise ValueError('Original merged-usr mapping inode required')

    alias_check()
    summary = []
    for name, (package, recorded) in FILES.items():
        path = PurePosixPath(name)
        parent = directory(path.parent)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE:
            raise ValueError('Bounded host package regular file required')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        entry = parent, path.name, fd, before, None
        regular(entry)
        data = A._read(fd, MAX_FILE)
        # Dpkg's legacy MD5 is only a local consistency observation, never authenticity.
        if len(data) != before.st_size or hashlib.md5(data, usedforsecurity=False).hexdigest() != pins[name]:
            raise ValueError('Host file differs from installed package metadata')
        pair = len(data), hashlib.sha256(data).hexdigest()
        regular(entry)
        files[name] = (*entry[:4], pair)
        summary.append({'path': name, 'package': package, 'recordedPath': recorded, 'size': pair[0], 'sha256': pair[1]})

    def check():
        A._root_linux()
        if _metadata()[0] != metadata:
            raise ValueError('Original installed package metadata changed')
        for fd, parent, name, identity in nodes.values():
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if S._directory(info, False) != identity:
                    raise ValueError('Original host package namespace required')
        alias_check()
        for entry in files.values():
            regular(entry)
            data = A._read(entry[2], entry[4][0])
            if (len(data), hashlib.sha256(data).hexdigest()) != entry[4]:
                raise ValueError('Held host package bytes changed')
            regular(entry)
        if _metadata()[0] != metadata:
            raise ValueError('Host package metadata changed during verification')

    check()
    return {'check': check, 'policy': {**budget, 'kind': 'held-host-package-file-consistency',
        'hostPackageFileMetadataMatched': True, 'hostPackageFilesObserved': len(files),
        'hostPackageVersions': dict(versions), 'hostPackageFileDigests': summary,
        **dict.fromkeys(('hostPackagePublisherAuthenticityVerified', 'hostPackageVersionCompatibilityVerified',
            'pamVendorBuildPathsVerified', 'pamModuleCodeVerified', 'effectivePamPolicyVerified',
            'effectiveSystemdNamespaceVerified', 'effectiveTmpfilesPolicyVerified', 'filesWritten',
            'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized',
            'installable', 'readyForActivation'), False)}}


def hold_final_host_package_files(stack, user, source, recipes, raw):
    A._root_linux()
    N.R.K.E.P.C._bounded(raw)
    packages = hold_host_package_files(stack)
    original = N.hold_final_support_namespace(stack, user, source, recipes, raw)

    def check():
        packages['check']()
        original['check']()
        packages['check']()

    def read(method, *args):
        check()
        data = original[method](*args)
        check()
        return data

    check()
    return {'check': check, 'read_plan': lambda: read('read_plan'), 'read_file': lambda name: read('read_file', name),
        'policy': {**original['policy'], **packages['policy'], 'kind': 'held-final-host-package-file-consistency'}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as stack:
        if argv == ['--inspect-host-package-files']:
            held = hold_host_package_files(stack)
        elif len(argv) == 5 and argv[0] == '--inspect-final-host-package-files' and argv[4] == '-':
            held = hold_final_host_package_files(stack, argv[1], argv[2], argv[3], sys.stdin.buffer.read(N.R.K.E.P.C.LIMIT + 1))
        else:
            raise ValueError('Only fixed read-only host package consistency inspection exposed')
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, N.R.K.E.P.configparser.Error):
        print('Host package consistency refused; no files or services changed', file=sys.stderr)
        sys.exit(1)
