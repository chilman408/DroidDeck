# SPDX-License-Identifier: GPL-3.0-only
"""New local HTTPS pair with explicit ownership-bound resume, never key export."""
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'OPENSSL_CONF': '/dev/null'}
STAGE = '.droiddeck-local-tls'
MODES = ('--initialize-local-tls', '--resume-local-tls', '--inspect-local-tls')


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def command(args, *, data=None, timeout=30):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=ENV, cwd='/', timeout=timeout, check=True)
    if len(result.stdout) > 160 * 1024 or len(result.stderr) > 16384:
        raise ValueError('Bounded TLS tool output required')
    return result.stdout


def policy(args, data=None):
    return json.loads(command(['/usr/bin/node', str(Path(__file__).with_name('local-tls-policy.js')), *args], data=data))


def generation(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
                'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


@contextmanager
def directories(path, uid, gid, *, create):
    import fcntl
    path = Path(path)
    if not path.is_absolute() or str(path) != str(path.resolve()) or '..' in path.parts:
        raise ValueError('Canonical local TLS preferences required')
    with ExitStack() as stack:
        parent, chain = None, []
        for index, name in enumerate(path.parts):
            current = Path(*path.parts[:index + 1])
            private = index == len(path.parts) - 1
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if not create or current.name not in ('.config', 'droiddeck'):
                    raise
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.fsync(fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
                    or info.st_mode & 0o022 or private and
                    (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, 0o700)):
                raise ValueError('Protected ancestors and private owned preferences required')
            chain.append((parent, name, fd, info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)))
            parent = fd

        def check():
            for above, name, opened, device, inode, owner, mode in chain:
                for info in (os.fstat(opened), os.stat(name, dir_fd=above, follow_symlinks=False)):
                    if (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)) != (device, inode, owner, mode):
                        raise ValueError('Original TLS preferences generation required')
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check()
        yield parent, check
        check()


def present(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def read(directory, name, uid, gid, mode, maximum):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink,
                stat.S_IMODE(info.st_mode)) != (uid, gid, 1, mode) or not 0 < info.st_size <= maximum):
            raise ValueError('Original private regular TLS file required')
        raw = os.read(fd, maximum + 1)
        if (len(raw) != info.st_size or generation(os.fstat(fd)) != generation(info)
                or generation(os.stat(name, dir_fd=directory, follow_symlinks=False)) != generation(info)):
            raise ValueError('Original TLS file generation required')
        return raw, info
    finally:
        os.close(fd)


def write(directory, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise ValueError('Incomplete TLS journal; preserve state')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory)


def rename_new(source, name, target, leaf):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(source, os.fsencode(name), target, os.fsencode(leaf), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    os.fsync(source)
    os.fsync(target)


def initialize(config, preferences, mode, *, after_intent=None, after_publish=None):
    if mode not in MODES:
        raise ValueError('Exact local TLS operation required')
    uid, gid = config['uid'], config['gid']
    with directories(preferences, uid, gid, create=mode == MODES[0]) as (parent, check):
        existing_stage = present(parent, STAGE)
        if existing_stage and mode == MODES[0]:
            raise ValueError('Existing TLS staging is never reinitialized; inspect and explicitly resume')
        if not existing_stage:
            if mode != MODES[0]:
                raise ValueError('Original local TLS initialization intent required')
            if any(present(parent, name) for name in ('key.pem', 'cert.pem')):
                raise ValueError('Existing TLS pair is never replaced or adopted')
            os.mkdir(STAGE, 0o700, dir_fd=parent)
            os.fsync(parent)
        stage = os.open(STAGE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            initial = os.fstat(stage)
            if (initial.st_uid, initial.st_gid, stat.S_IMODE(initial.st_mode)) != (uid, gid, 0o700):
                raise ValueError('Private user-owned TLS staging required')

            def guard():
                check()
                info = os.stat(STAGE, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                        initial.st_dev, initial.st_ino, uid, gid, 0o700):
                    raise ValueError('Original TLS staging directory required')

            if mode == MODES[0]:
                if os.listdir(stage):
                    raise ValueError('Existing TLS initialization requires explicit resume')
                guard()
                # Key never leaves this new user-private stage or enters a receipt.
                key = f'/proc/self/fd/{stage}/key.pem'
                cert = f'/proc/self/fd/{stage}/cert.pem'
                result = subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                    '-noenc', '-days', '365', '-subj', '/CN=DroidDeck', '-keyout', key, '-out', cert,
                    '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'extendedKeyUsage=serverAuth',
                    '-addext', 'keyUsage=critical,digitalSignature,keyEncipherment',
                    '-addext', 'subjectAltName=DNS:' + config['hostname'] + ',IP:' + config['lanAddress']],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, pass_fds=(stage,), env=ENV,
                    cwd='/', timeout=30, check=True, umask=0o077)
                if len(result.stderr) > 16384:
                    raise ValueError('Bounded certificate generation required')
                rows = {}
                for name in ('key.pem', 'cert.pem'):
                    raw, info = read(stage, name, uid, gid, 0o600, 128 * 1024)
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=stage)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    rows[name] = {'device': info.st_dev, 'inode': info.st_ino, 'size': len(raw)}
                    if name == 'cert.pem':
                        rows[name]['sha256'] = hashlib.sha256(raw).hexdigest()
                record = {'version': 1, 'kind': 'new-local-tls-pair', 'configuration': config, 'files': rows}
                write(stage, 'intent.json', encode(record))
                if after_intent:
                    after_intent()
            raw, intent_info = read(stage, 'intent.json', uid, gid, 0o600, 16384)
            record = json.loads(raw)
            if (encode(record) != raw or set(record) != {'version', 'kind', 'configuration', 'files'}
                    or type(record['version']) is not int or record['version'] != 1
                    or record['kind'] != 'new-local-tls-pair' or encode(record['configuration']) != encode(config)
                    or set(record['files']) != {'key.pem', 'cert.pem'}
                    or set(os.listdir(stage)) - {'key.pem', 'cert.pem', 'intent.json'}):
                raise ValueError('Original local TLS intent required')

            def observe():
                guard()
                original, info = read(stage, 'intent.json', uid, gid, 0o600, 16384)
                if original != raw or generation(info) != generation(intent_info):
                    raise ValueError('Original TLS intent generation required')
                found, payload = {}, {}
                for name, row in record['files'].items():
                    keys = {'device', 'inode', 'size'} | ({'sha256'} if name == 'cert.pem' else set())
                    if set(row) != keys or any(type(row[k]) is not int or row[k] <= 0 for k in ('device', 'inode', 'size')):
                        raise ValueError('Exact TLS original file identity required')
                    locations = [directory for directory in (stage, parent) if present(directory, name)]
                    if len(locations) != 1:
                        raise ValueError('Ambiguous or missing TLS file; preserve all state')
                    data, info = read(locations[0], name, uid, gid, 0o600, 128 * 1024)
                    if ((info.st_dev, info.st_ino, info.st_size) != (row['device'], row['inode'], row['size'])
                            or name == 'cert.pem' and hashlib.sha256(data).hexdigest() != row['sha256']):
                        raise ValueError('Original staged TLS file required')
                    found[name], payload[name] = locations[0], data.decode('ascii')
                receipt = policy(['--validate'], encode({'configuration': config,
                    'key': payload['key.pem'], 'certificate': payload['cert.pem']}))
                return found, receipt

            locations, receipt = observe()
            if mode == MODES[2] and any(value != parent for value in locations.values()):
                raise ValueError('Local TLS publication incomplete')
            if mode != MODES[2]:
                for name in ('key.pem', 'cert.pem'):
                    locations, receipt = observe()
                    if locations[name] == stage:
                        rename_new(stage, name, parent, name)
                        if after_publish:
                            after_publish(name)
            locations, receipt = observe()
            return {**receipt, 'kind': 'local-tls-initialization', 'pairPublished': True,
                    'servicesActivated': False, 'privateKeyExported': False}
        finally:
            os.close(stage)


def main(args):
    if sys.platform != 'linux' or os.geteuid() == 0 or len(args) != 1 or args[0] not in MODES:
        raise ValueError('Configured unprivileged Linux account and fixed TLS operation required')
    import pwd
    config = policy(['--configuration'])
    account = pwd.getpwnam(config['user'])
    if ((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (config['uid'], config['uid'], config['gid'], config['gid'])
            or (account.pw_uid, account.pw_gid, account.pw_dir) != (config['uid'], config['gid'], config['home'])):
        raise ValueError('Configured existing account identity required')
    print(json.dumps(initialize(config, config['home'] + '/.config/droiddeck', args[0]), sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Local TLS incomplete; preserve private staged files and original intent; key material redacted', file=sys.stderr)
        sys.exit(1)
