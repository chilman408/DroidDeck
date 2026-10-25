# SPDX-License-Identifier: GPL-3.0-only
"""Required baseline apps: install missing Aurora only, never update existing apps."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

PACKAGE = 'com.aurora.store'
VERSION = '4.8.4'
VERSION_CODE = 76
SIZE = 9362660
SHA256 = '8a1ed9aa09631290da91cb793e0517b0f20dc70239ac94ae6682cd94f91a4bad'
CERTIFICATE = '4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f'
PUBLISHER = 'https://auroraoss.com/downloads/AuroraStore/Release/AuroraStore-4.8.4.apk'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'JAVA_HOME': '/usr/lib/jvm/java-17-openjdk-amd64'}


def run(args, *, stdin=subprocess.DEVNULL, pass_fds=(), timeout=30):
    result = subprocess.run(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=timeout, env=ENV, cwd='/', pass_fds=pass_fds)
    if len(result.stdout) > 256 * 1024 or len(result.stderr) > 256 * 1024:
        raise ValueError('Bounded app admission output required')
    return result.stdout.decode('utf-8', errors='strict').strip()


def signer(output):
    values = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]{64})$', output, re.M)
    if [value.lower() for value in values] != [CERTIFICATE]:
        raise ValueError('Official Aurora signing certificate required')


def badging(output):
    line = output.splitlines()[0] if output else ''
    match = re.match(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'(?: |$)", line)
    if not match or match.groups() != (PACKAGE, str(VERSION_CODE), VERSION):
        raise ValueError('Exact required Aurora package/version required')


@contextmanager
def hold_apk(filename):
    path = Path(filename)
    if not path.is_absolute() or str(path) != filename or '..' in path.parts:
        raise ValueError('Canonical protected APK path required')
    descriptors = []
    try:
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(parent)
        chain = []
        for name in path.parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
                raise ValueError('Root-owned nonwritable APK ancestors required')
            chain.append((parent, name, fd, info.st_dev, info.st_ino))
            parent = fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(fd)
        original = os.fstat(fd)

        def check():
            for parent_fd, name, opened, device, inode in chain:
                for value in (os.fstat(opened), os.stat(name, dir_fd=parent_fd, follow_symlinks=False)):
                    if (not stat.S_ISDIR(value.st_mode) or (value.st_uid, value.st_gid) != (0, 0)
                            or value.st_mode & 0o022 or (value.st_dev, value.st_ino) != (device, inode)):
                        raise ValueError('Protected APK ancestor changed')
            for value in (os.fstat(fd), os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
                if (not stat.S_ISREG(value.st_mode) or (value.st_uid, value.st_gid, value.st_nlink,
                        stat.S_IMODE(value.st_mode), value.st_size) != (0, 0, 1, 0o600, SIZE)
                        or any(getattr(value, field) != getattr(original, field) for field in
                            ('st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns'))):
                    raise ValueError('Original private single-link Aurora APK required')
            if hashlib.sha256(os.pread(fd, SIZE + 1, 0)).hexdigest() != SHA256:
                raise ValueError('Independently pinned Aurora APK bytes required')
        check()
        yield fd, check
        check()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class Guest:
    def __init__(self):
        value = run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])
        if not re.fullmatch('[1-9][0-9]*', value) or int(value) < 2:
            raise ValueError('Running Android required')
        self.pid = value
        self.identity = Path('/proc', value).stat().st_ino, Path('/proc', value, 'stat').read_text().rsplit(')', 1)[1].split()[19]
        self.check()

    def check(self):
        pid = run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])
        current = Path('/proc', self.pid).stat().st_ino, Path('/proc', self.pid, 'stat').read_text().rsplit(')', 1)[1].split()[19]
        if pid != self.pid or current != self.identity:
            raise ValueError('Original Android container generation required')

    def command(self, args, **options):
        self.check()
        try:
            return run(['/usr/bin/nsenter', '-t', self.pid, '-m', '-p', '-r', '--', *args], **options)
        finally:
            self.check()

    def inspect(self):
        try:
            output = self.command(['/system/bin/pm', 'path', '--user', '0', PACKAGE])
        except subprocess.CalledProcessError as error:
            # Android pm path uses exit1 with empty streams for an absent package.
            if error.returncode != 1 or error.stdout or error.stderr:
                raise
            output = ''
        if not output:
            return {'package': PACKAGE, 'requiredByDefault': True, 'installed': False, 'defaultsSatisfied': False}
        paths = output.splitlines()
        if (len(paths) != 1 or not re.fullmatch(r'package:/(?:data/app|system/app|product/app)/[A-Za-z0-9_./=+~-]+\.apk', paths[0])
                or '..' in paths[0].split('/')):
            raise ValueError('Single installed Aurora base APK required')
        filename = '/proc/' + self.pid + '/root' + paths[0].removeprefix('package:')
        signer(run(['/usr/bin/apksigner', 'verify', '--print-certs', filename]))
        activity = self.command(['/system/bin/cmd', 'package', 'resolve-activity', '--brief', '--user', '0', PACKAGE])
        if PACKAGE + '/' not in activity:
            raise ValueError('Aurora launch activity required')
        return {'package': PACKAGE, 'requiredByDefault': True, 'installed': True,
            'publisherSignatureVerified': True, 'launchable': True, 'defaultsSatisfied': True}


def install_missing(guest, filename):
    before = guest.inspect()
    if before['installed']:
        return {**before, 'installedNow': False, 'existingAppPreserved': True}
    with hold_apk(filename) as (fd, check):
        signer(run(['/usr/bin/apksigner', 'verify', '--print-certs', '/proc/self/fd/' + str(fd)], pass_fds=(fd,)))
        badging(run(['/usr/bin/aapt', 'dump', 'badging', '/proc/self/fd/' + str(fd)], pass_fds=(fd,)))
        check()
        if guest.inspect()['installed']:
            raise ValueError('Aurora appeared during admission; preserve it')
        os.lseek(fd, 0, os.SEEK_SET)
        # No replace/downgrade/grant flags, other packages, shell interpolation or app-data copies.
        result = guest.command(['/system/bin/pm', 'install', '--user', '0', '-S', str(SIZE)], stdin=fd, timeout=120)
        if result != 'Success':
            raise ValueError('Android installation did not report success')
        check()
    after = guest.inspect()
    if not after['defaultsSatisfied']:
        raise ValueError('Required default Aurora installation did not verify')
    return {**after, 'installedNow': True, 'appVersion': VERSION, 'accountsConfigured': False,
        'automaticAppUpdatesConfigured': False, 'otherAppsChanged': False}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or not (args == ['--inspect'] or len(args) == 2 and args[0] == '--install-missing'):
        raise ValueError('Root Linux default-app inspect or install-missing APK required')
    guest = Guest()
    result = guest.inspect() if args == ['--inspect'] else install_missing(guest, args[1])
    print(json.dumps(result, sort_keys=True))
    return 0 if result['defaultsSatisfied'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        print('Required default-app verification incomplete; preserve existing apps and data', file=sys.stderr)
        sys.exit(1)
