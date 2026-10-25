# SPDX-License-Identifier: GPL-3.0-only
"""One-time clean-install swap reserve; inspect metadata, never swap contents."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

spec = importlib.util.spec_from_file_location('swap_host', Path(__file__).with_name('host-prerequisites.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
ROOT = Path('/')
SIZE = 4 * 1024 ** 3
MINIMUM = SIZE - 4096
HEADROOM = 12 * 1024 ** 3
UNIT = 'swapfile.swap'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}


def run(args, *, pass_fds=()):
    return subprocess.run(list(map(str, args)), cwd='/', env=ENV, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, timeout=120, check=True, pass_fds=pass_fds).stdout


def inventory(runner):
    # Installed swapon has no JSON mode. Fixed raw columns escape path whitespace.
    raw = runner(['/usr/sbin/swapon', '--show=SIZE,NAME', '--bytes', '--raw', '--noheadings'])
    if len(raw) > 65536:
        raise ValueError('Bounded kernel swap inventory required')
    result = {}
    for row in raw.decode('ascii').splitlines():
        match = re.fullmatch(r'([1-9][0-9]{0,19}) (/[^\s]+)', row)
        if not match or match[2] in result:
            raise ValueError('Unique fixed-column swap metadata required')
        result[match[2]] = int(match[1])
    return result


class Reserve:
    def __init__(self, source, fd, runner=run):
        self.source, self.fd, self.run = Path(source), fd, runner
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Original private root installation journal required')

    def record(self, name, value):
        H.write_new(self.fd, 'host-swap.' + name + '.json', H.encode(value))

    def read(self, name):
        fd = os.open('host-swap.' + name + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 4096):
                raise ValueError('Protected bounded swap journal required')
            raw = os.read(fd, 4097)
            value = json.loads(raw)
            if H.encode(value) != raw:
                raise ValueError('Canonical swap journal required')
            return value
        finally:
            os.close(fd)

    def unit_bytes(self):
        return H.protected(self.source / UNIT, maximum=4096)

    def open_roots(self):
        root = H.protected(ROOT, directory=True)
        try:
            systemd = H.protected(ROOT / 'etc/systemd/system', directory=True)
        except Exception:
            os.close(root)
            raise
        return root, systemd

    @staticmethod
    def absent(fd, name):
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('Pre-existing swap file/unit is never adopted or replaced')

    @staticmethod
    def identity(info):
        return {'device': info.st_dev, 'inode': info.st_ino}

    def check_file(self, root, original):
        info = os.stat('swapfile', dir_fd=root, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size != SIZE or info.st_blocks * 512 < SIZE
                or self.identity(info) != original):
            raise ValueError('Original allocated root0600 swap inode required')

    def check_loaded_unit(self):
        raw = self.run(['/usr/bin/systemctl', 'show', UNIT, '-p', 'FragmentPath', '-p', 'DropInPaths'])
        expected = {'FragmentPath=' + str(ROOT / 'etc/systemd/system' / UNIT), 'DropInPaths='}
        if len(raw) > 4096 or set(raw.decode('ascii').splitlines()) != expected:
            raise ValueError('Exact owned effective swap unit without drop-ins required')

    def prepare(self):
        raw = self.unit_bytes()
        swaps = inventory(self.run)
        intent = {'schema': 1, 'mode': 'existing-system-swap' if swaps else 'new-owned-reserve',
                  'minimumBytes': MINIMUM, 'unitSha256': hashlib.sha256(raw).hexdigest()}
        if swaps:
            if sum(swaps.values()) < MINIMUM:
                raise ValueError('Existing system swap is insufficient; preserve it for explicit host setup')
            self.record('intent', intent)
            self.record('complete', intent)
            return
        root, systemd = self.open_roots()
        try:
            self.absent(root, 'swapfile')
            self.absent(systemd, UNIT)
            if self.run(['/usr/bin/systemctl', 'show', UNIT, '-p', 'LoadState', '--value']) != b'not-found\n':
                raise ValueError('No pre-existing generated, transient or vendor swap unit allowed')
            mount = json.loads(self.run(['/usr/bin/findmnt', '--json', '--target', str(ROOT), '--output', 'TARGET,FSTYPE']))
            if mount != {'filesystems': [{'target': str(ROOT), 'fstype': 'ext4'}]}:
                raise ValueError('Only an explicit ext4 root supports automatic swap provisioning')
            space = os.fstatvfs(root)
            if space.f_bavail * space.f_frsize < HEADROOM:
                raise ValueError('At least12GiB free space required before allocating4GiB')
            self.record('intent', intent)
            swap = os.open('swapfile', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
            try:
                original = self.identity(os.fstat(swap))
                os.fsync(root)
                self.record('file', original)
                os.posix_fallocate(swap, 0, SIZE)
                os.fsync(swap)
                self.check_file(root, original)
                # Format ONLY this exclusively created, retained descriptor.
                self.run(['/usr/sbin/mkswap', '/proc/self/fd/' + str(swap)], pass_fds=(swap,))
                os.fsync(swap)
                self.check_file(root, original)
            finally:
                os.close(swap)
            H.write_new(systemd, UNIT, raw)
            unit = os.open(UNIT, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=systemd)
            try:
                os.fchmod(unit, 0o644)
                os.fsync(unit)
                self.record('unit', self.identity(os.fstat(unit)))
            finally:
                os.close(unit)
            os.fsync(systemd)
            self.check_file(root, original)
            self.run(['/usr/bin/systemd-analyze', 'verify', str(ROOT / 'etc/systemd/system' / UNIT)])
            self.run(['/usr/bin/systemctl', 'daemon-reload'])
            self.check_loaded_unit()
            self.run(['/usr/bin/systemctl', 'enable', '--now', UNIT])
            self.verify(intent)
            self.record('complete', intent)
        finally:
            os.close(systemd)
            os.close(root)

    def verify(self, intent):
        if intent != {'schema': 1, 'mode': intent.get('mode'), 'minimumBytes': MINIMUM,
                      'unitSha256': hashlib.sha256(self.unit_bytes()).hexdigest()}:
            raise ValueError('Original fixed swap policy required')
        swaps = inventory(self.run)
        if intent['mode'] == 'existing-system-swap':
            if sum(swaps.values()) < MINIMUM:
                raise ValueError('Existing system swap reserve no longer active')
            return
        if intent['mode'] != 'new-owned-reserve':
            raise ValueError('Known original swap policy required')
        root, systemd = self.open_roots()
        try:
            self.check_file(root, self.read('file'))
            path = ROOT / 'etc/systemd/system' / UNIT
            if (H.protected(path, maximum=4096) != self.unit_bytes()
                    or self.identity(path.lstat()) != self.read('unit')
                    or stat.S_IMODE(path.lstat().st_mode) != 0o644):
                raise ValueError('Original root0644 swap unit required')
            self.check_loaded_unit()
            if (swaps.get(str(ROOT / 'swapfile'), 0) < MINIMUM
                    or self.run(['/usr/bin/systemctl', 'is-active', UNIT]) != b'active\n'
                    or self.run(['/usr/bin/systemctl', 'is-enabled', UNIT]) != b'enabled\n'):
                raise ValueError('Owned swap must be active and persistently enabled')
        finally:
            os.close(systemd)
            os.close(root)

    def inspect(self):
        intent = self.read('intent')
        if self.read('complete') != intent:
            raise ValueError('Original completed swap preparation required; never replay partial work')
        self.verify(intent)
