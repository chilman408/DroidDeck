# SPDX-License-Identifier: GPL-3.0-only
"""Acquire fixed public publisher inputs into new unprivileged workspaces."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).parents[2]
LOCK = json.loads((ROOT / 'config/source-lock.json').read_text())
IMAGES = json.loads((ROOT / 'config/portable-image-inputs.json').read_text())
COMPONENTS = {item['path']: item for item in LOCK['tesla_components']}
REPOSITORIES = {
    'libws': (COMPONENTS['external/libws']['url'], COMPONENTS['external/libws']['revision']),
    'api': (COMPONENTS['external/tesla-android-configuration-manager']['url'],
            COMPONENTS['external/tesla-android-configuration-manager']['revision']),
    'gbinder': ('https://github.com/mer-hybris/libgbinder.git', '08252c411e63f89c2a7b056ed5bdd0b9728704ec'),
    'glibutil': ('https://github.com/sailfishos/libglibutil.git', 'e2fe548f8cce21551fd59a461618ea586b5f98fb'),
    'frontend': (LOCK['frontend']['url'], LOCK['frontend']['revision']),
}
DOWNLOADS = {
    'r8.jar': ('https://storage.googleapis.com/r8-releases/raw/8.13.17/r8lib.jar',
               'd31fd0dc751d48740009cdd9a485126acb1d0d14c59b9f05579479940f4adf74', 64 * 1024 ** 2),
    'platform-36_r02.zip': ('https://dl.google.com/android/repository/platform-36_r02.zip',
                          '37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e', 256 * 1024 ** 2),
    'scrcpy-server-v4.0': ('https://github.com/Genymobile/scrcpy/releases/download/v4.0/scrcpy-server-v4.0',
                          '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a', 1024 ** 2),
    'AuroraStore-4.8.4.apk': ('https://auroraoss.com/downloads/AuroraStore/Release/AuroraStore-4.8.4.apk',
                            '8a1ed9aa09631290da91cb793e0517b0f20dc70239ac94ae6682cd94f91a4bad', 16 * 1024 ** 2),
}
MODES = ('--acquire-build-inputs', '--acquire-image-inputs')
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
       'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_ALLOW_PROTOCOL': 'https', 'GIT_NO_LAZY_FETCH': '1'}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        value = urllib.parse.urlsplit(newurl)
        if value.scheme != 'https' or value.username or value.password or value.port not in (None, 443):
            raise ValueError('HTTPS-only publisher redirect required')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def write_new(directory, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory)


def download(directory, name, url, expected, maximum, *, opener=None):
    if not url.startswith('https://') or '/' in name or name in ('.', '..'):
        raise ValueError('Fixed HTTPS publisher input required')
    # The pending file is never overwritten, including after an interrupted download.
    pending = name + '.partial'
    fd = os.open(pending, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        before = os.fstat(fd)
        free = os.fstatvfs(directory)
        if free.f_bavail * free.f_frsize < maximum + 64 * 1024 ** 2:
            raise ValueError('Insufficient private download capacity')
        open_url = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect()).open
        digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 900
        request = urllib.request.Request(url, headers={'User-Agent': 'DroidDeck-input-acquisition/0.1',
                                                      'Accept-Encoding': 'identity'})
        with open_url(request, timeout=30) as response, os.fdopen(fd, 'wb', closefd=False) as output:
            if response.status != 200:
                raise ValueError('Successful publisher response required')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or not 0 < int(length) <= maximum):
                raise ValueError('Bounded publisher input required')
            while block := response.read(1024 ** 2):
                count += len(block)
                if count > maximum or time.monotonic() > deadline:
                    raise ValueError('Publisher input exceeded bounds')
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(fd)
        if not count or length is not None and int(length) != count or digest.hexdigest() != expected:
            raise ValueError('Independent publisher input digest required')
        current = os.stat(pending, dir_fd=directory, follow_symlinks=False)
        if ((current.st_dev, current.st_ino, current.st_size, current.st_nlink) !=
                (before.st_dev, before.st_ino, count, 1) or not stat.S_ISREG(current.st_mode)):
            raise ValueError('Original private publisher file required')
        os.link('/proc/self/fd/' + str(fd), name, dst_dir_fd=directory, follow_symlinks=True)
        os.unlink(pending, dir_fd=directory)
        os.fsync(directory)
        return {'name': name, 'size': count, 'sha256': expected, 'publisher': url}
    finally:
        os.close(fd)


def git(args):
    result = subprocess.run(['/usr/bin/git', '--no-replace-objects', '--no-lazy-fetch',
        '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=', '-c', 'protocol.file.allow=never',
        *args], env=ENV, cwd='/', stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=180, check=True)
    if len(result.stdout) > 65536 or len(result.stderr) > 1024 ** 2:
        raise ValueError('Bounded publisher Git output required')
    return result.stdout


def repository(root, name, url, revision):
    destination = root / (name + '.git')
    destination.mkdir(mode=0o700)
    git(['init', '--bare', '--template=', str(destination)])
    git(['--git-dir=' + str(destination), 'fetch', '--no-tags', '--depth=1', url, revision])
    actual = git(['--git-dir=' + str(destination), 'rev-parse', '--verify', revision + '^{commit}']).decode().strip()
    if actual != revision:
        raise ValueError('Exact publisher commit required')
    return {'name': name + '.git', 'publisher': url, 'revision': actual}


def sdk(root, directory):
    # Extract only the three reviewed public SDK members, never ZipFile.extractall.
    rows = []
    with zipfile.ZipFile(root / 'platform-36_r02.zip') as archive:
        for name in ('android.jar', 'source.properties', 'data/NOTICE.txt'):
            member = 'android-36/' + name
            found = [entry for entry in archive.infolist() if entry.filename == member]
            if len(found) != 1 or found[0].is_dir() or found[0].flag_bits & 1 or not 0 < found[0].file_size <= 64 * 1024 ** 2:
                raise ValueError('Exact bounded public SDK member required')
            raw = archive.read(found[0])
            if name == 'android.jar' and hashlib.sha256(raw).hexdigest() != 'd9eb9da824d9e247a352f570f01e1169e725b2954bca9e283a71786c59b59f9a':
                raise ValueError('Independently pinned Android API jar required')
            leaf = 'android.jar' if name == 'android.jar' else 'sdk-' + Path(name).name
            write_new(directory, leaf, raw)
            rows.append({'name': leaf, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return rows


def acquire(mode, output, *, fetch=download, clone=repository):
    if mode not in MODES:
        raise ValueError('Exact publisher acquisition mode required')
    import fcntl
    root = Path(output)
    home = Path.home()
    if (not root.is_absolute() or str(root) != str(root.resolve()) or not root.is_relative_to(home)
            or root == home):
        raise ValueError('New canonical workspace below current user home required')
    with ExitStack() as stack:
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        ancestors = []
        for name in root.parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise ValueError('Protected publisher workspace ancestors required')
            ancestors.append((parent, name, fd, info.st_dev, info.st_ino))
            parent = fd
        os.mkdir(root.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        directory = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, directory)
        initial = os.fstat(directory)
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def check():
            for above, name, held, device, inode in (*ancestors, (parent, root.name, directory, initial.st_dev, initial.st_ino)):
                for info in (os.fstat(held), os.stat(name, dir_fd=above, follow_symlinks=False)):
                    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 or
                            info.st_uid not in (0, os.getuid()) or (info.st_dev, info.st_ino) != (device, inode)):
                        raise ValueError('Original publisher workspace generation required')
        files = []
        plan = DOWNLOADS if mode == MODES[0] else {
            item['filename']: (item['url'], item['sha256'], 8 * 1024 ** 3) for item in IMAGES['inputs'].values()}
        write_new(directory, 'intent.json', encode({'version': 1, 'mode': mode, 'publishers': plan,
                                                   'repositories': REPOSITORIES if mode == MODES[0] else {}}))
        for name, (url, digest, bound) in plan.items():
            check()
            print('Acquiring ' + name, file=sys.stderr, flush=True)
            files.append(fetch(directory, name, url, digest, bound))
            check()
        repositories = []
        if mode == MODES[0]:
            files.extend(sdk(root, directory))
            for name, (url, revision) in REPOSITORIES.items():
                check()
                print('Acquiring ' + name + ' publisher source', file=sys.stderr, flush=True)
                repositories.append(clone(root, name, url, revision))
                check()
            inputs = {key: str(root / value) for key, value in {
                'libwsRepo': 'libws.git', 'apiRepo': 'api.git', 'gbinderRepo': 'gbinder.git',
                'glibutilRepo': 'glibutil.git', 'd8Jar': 'r8.jar', 'androidJar': 'android.jar',
                'scrcpyServer': 'scrcpy-server-v4.0'}.items()}
            write_new(directory, 'inputs.json', encode(inputs))
        check()
        result = {'version': 1, 'kind': 'publisher-input-acquisition', 'mode': mode,
                  'files': files, 'repositories': repositories, 'installable': False,
                  'redistribute': False, 'servicesActivated': False, 'androidOperationsExecuted': False}
        write_new(directory, 'ready.json', encode(result))
        check()
        return result


def main(args):
    if sys.platform != 'linux' or os.geteuid() == 0 or len(args) != 2 or args[0] not in MODES:
        raise ValueError('Unprivileged Linux fixed acquisition operation and NEW_OUTPUT required')
    previous = os.umask(0o077)
    try:
        print(json.dumps(acquire(*args), sort_keys=True))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Publisher acquisition incomplete; preserve any new workspace; existing files never overwritten', file=sys.stderr)
        sys.exit(1)
