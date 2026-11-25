# SPDX-License-Identifier: GPL-3.0-only
"""Renew an expiring self-signed LAN certificate without replacing its private key.

Run as the account owning the private TLS directory. Public certificates only are
backed up. This tool never renews a CA-issued certificate or changes services.
"""
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'OPENSSL_CONF': '/dev/null'}
WINDOW = 30 * 86400


def run(args, *, data=None, fds=(), allowed=(0,)):
    p = subprocess.run(['/usr/bin/openssl', *args], input=data, capture_output=True,
                       pass_fds=fds, env=ENV, cwd='/', timeout=30)
    if p.returncode not in allowed or len(p.stdout) > 131072 or len(p.stderr) > 16384:
        raise ValueError('Local certificate operation refused; key material redacted')
    return p


def generation(info):
    return tuple(getattr(info, k) for k in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def renew(preferences, address):
    address = str(ipaddress.IPv4Address(address))
    path = Path(preferences)
    uid = os.geteuid()
    if uid == 0 or not path.is_absolute() or str(path.resolve()) != str(path):
        raise ValueError('Canonical private directory and unprivileged owner required')
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    temporary = None
    try:
        ds = os.fstat(directory)
        if ds.st_uid != uid or stat.S_IMODE(ds.st_mode) != 0o700:
            raise ValueError('Private user-owned TLS directory required')
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        files = {}
        for name in ('key.pem', 'cert.pem'):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            opened.append(fd)
            s = os.fstat(fd)
            modes = (0o600,) if name == 'key.pem' else (0o600, 0o644, 0o664)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != uid or s.st_gid != os.getegid()
                    or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) not in modes
                    or not 0 < s.st_size <= 131072):
                raise ValueError('Owned regular local TLS pair required')
            files[name] = (fd, s)

        def check():
            now = path.lstat()
            if (now.st_dev, now.st_ino, now.st_uid, stat.S_IMODE(now.st_mode)) != (
                    ds.st_dev, ds.st_ino, uid, 0o700):
                raise ValueError('TLS directory changed')
            for name, (fd, original) in files.items():
                if generation(os.fstat(fd)) != generation(original) or generation(
                        os.stat(name, dir_fd=directory, follow_symlinks=False)) != generation(original):
                    raise ValueError('TLS pair changed during renewal')

        check()
        keyfd, _ = files['key.pem']; certfd, original = files['cert.pem']
        key = f'/proc/self/fd/{keyfd}'; cert = f'/proc/self/fd/{certfd}'
        fds = (keyfd, certfd)
        issuer = run(['x509', '-in', cert, '-noout', '-issuer', '-nameopt', 'RFC2253'], fds=fds).stdout.partition(b'=')[2]
        subject = run(['x509', '-in', cert, '-noout', '-subject', '-nameopt', 'RFC2253'], fds=fds).stdout.partition(b'=')[2]
        if issuer != subject:
            raise ValueError('CA-issued certificates require their original renewal provider')
        run(['verify', '-no_check_time', '-check_ss_sig', '-CAfile', cert, '-verify_ip', address, cert], fds=fds)
        public = run(['pkey', '-in', key, '-pubout'], fds=fds).stdout
        if public != run(['x509', '-in', cert, '-pubkey', '-noout'], fds=fds).stdout:
            raise ValueError('Certificate and existing key differ')
        if run(['x509', '-in', cert, '-noout', '-checkend', str(WINDOW)], fds=fds, allowed=(0, 1)).returncode == 0:
            check()
            return {'renewed': False, 'privateKeyReplaced': False}
        request = run(['x509', '-x509toreq', '-in', cert, '-signkey', key,
                       '-copy_extensions', 'copyall'], fds=fds).stdout
        replacement = run(['req', '-x509', '-in', '/dev/stdin', '-key', key, '-days', '365', '-sha256',
                           '-copy_extensions', 'copyall'], data=request, fds=fds).stdout
        if run(['x509', '-pubkey', '-noout'], data=replacement).stdout != public:
            raise ValueError('Renewal changed the public key')
        previous = os.pread(certfd, 131073, 0)
        if len(previous) != original.st_size:
            raise ValueError('Bounded certificate required')
        backup = 'cert-before-renewal-' + hashlib.sha256(previous).hexdigest() + '.pem'
        try:
            backupfd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            backupfd = os.open(backup, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(backupfd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600 or os.read(backupfd, 131073) != previous):
                    raise ValueError('Original public certificate backup required')
            finally:
                os.close(backupfd)
        else:
            with os.fdopen(backupfd, 'wb') as stream:
                stream.write(previous); stream.flush(); os.fsync(stream.fileno())
        temporary = '.cert-renew-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(replacement); stream.flush(); os.fsync(stream.fileno())
        new = f'/proc/self/fd/{directory}/{temporary}'
        run(['verify', '-check_ss_sig', '-CAfile', new, '-verify_ip', address, new], fds=(directory,))
        run(['x509', '-in', new, '-noout', '-checkend', str(WINDOW)], fds=(directory,))
        check()
        os.replace(temporary, 'cert.pem', src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        os.fsync(directory)
        return {'renewed': True, 'privateKeyReplaced': False,
                'certificateSha256': hashlib.sha256(replacement).hexdigest()}
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory)
        for fd in opened:
            os.close(fd)
        os.close(directory)


if __name__ == '__main__':
    try:
        if sys.platform != 'linux' or len(sys.argv) != 3:
            raise ValueError('Private TLS directory and LAN IPv4 address required')
        print(json.dumps(renew(*sys.argv[1:]), sort_keys=True))
    except Exception:
        print('Local TLS renewal refused; original pair retained; private material redacted', file=sys.stderr)
        sys.exit(1)
