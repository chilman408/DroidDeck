# SPDX-License-Identifier: GPL-3.0-only
"""Clean-machine installation composition; original component journals survive failures."""
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import select
import signal
import stat
import subprocess
import sys
import time


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


S, U = load('stage-installer-sources'), load('prepare-user-session')
H = S.H
STATE = Path('/root/droiddeck-installation')
ETC = Path('/etc')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C',
       'JAVA_HOME': '/usr/lib/jvm/java-17-openjdk-amd64'}
UNITS = ('tesla-waydroid-runtime.service', 'tesla-waydroid-headless-session.service',
         'waydroid-container.service', 'tesla-waydroid-compositor.service')


def configuration(user, address, hostname):
    value = U.account(user)
    ip = ipaddress.IPv4Address(address)
    if (str(ip) != address or ip.packed[-1] in (0, 255) or not any(ip in block for block in
            (ipaddress.IPv4Network('10.0.0.0/8'), ipaddress.IPv4Network('172.16.0.0/12'), ipaddress.IPv4Network('192.168.0.0/16')))
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', hostname)
            or any(not 1 <= len(label) <= 63 or label.startswith('-') or label.endswith('-') for label in hostname.split('.'))):
        raise ValueError('Exact private IPv4 address and DNS hostname required')
    return {'version': 1, **value, 'lanAddress': address, 'port': 8443, 'hostname': hostname}


def user_command(config, args):
    return ['/usr/sbin/runuser', '-u', config['user'], '--', '/usr/bin/env', '-i',
            'PATH=/usr/bin:/bin', 'HOME=' + config['home'], 'LANG=C', 'LC_ALL=C',
            'JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64', *map(str, args)]


class Install:
    def __init__(self, package, config, fd):
        self.package, self.config, self.fd = Path(package), config, fd
        self.source = self.package / 'src/runtime/portable'
        self.work = Path(config['home']) / 'droiddeck-installation'
        self.assembly = STATE / 'protected/artifact-stage/assembly'
        self.recipes = S.ROOT / 'recipes'
        self.compat = S.ROOT / 'compat-source.js'
        self.frontend = self.work / 'prepared/frontend'
        self.raw = H.encode(config)
        self.phase = None

    def run(self, args, *, data=None, timeout=900):
        result = subprocess.run(list(map(str, args)), input=data, stdout=subprocess.PIPE,
                                stderr=None, cwd='/', env=ENV, timeout=timeout, check=True)
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('Bounded installation tool output required')
        return result.stdout

    def cli(self, name, *args, user=False, data=None):
        command = ['/usr/bin/python3', '-I', '-B', self.source / name, *args]
        return self.run(user_command(self.config, command) if user else command, data=data)

    def phase_run(self, name, operation):
        self.phase = name
        # No guessed replay: a failed clean install preserves every original
        # journal and partial output for its component's explicit recovery CLI.
        H.write_new(self.fd, name + '.started.json', H.encode({'phase': name}))
        print('Installing: ' + name, file=sys.stderr, flush=True)
        value = operation()
        H.write_new(self.fd, name + '.complete.json', H.encode({'phase': name}))
        return value

    def runtime_args(self):
        return (self.config['user'], self.assembly, self.recipes, self.frontend, self.compat, '-')

    def prepare_directories(self):
        self.restart_policy()
        self.swap_reserve()
        self.work.mkdir(mode=0o700)
        os.chown(self.work, self.config['uid'], self.config['gid'])
        for path in ('/var/lib/droiddeck', '/var/lib/droiddeck/image-staging', '/etc/waydroid-extra'):
            Path(path).mkdir(mode=0o700)
        self.run(['/usr/bin/systemctl', 'disable', 'waydroid-container.service'])

    def restart_policy(self, *, inspect=False):
        # Deferring only coupled graph restarts must precede Android startup.
        # Never adopt an existing drop-in, even when its bytes happen to match.
        raw = H.protected(self.source / 'needrestart-droiddeck.conf', maximum=4096)
        directory = H.protected(ETC, directory=True)
        try:
            for name in ('needrestart', 'conf.d'):
                if not inspect:
                    try:
                        os.mkdir(name, mode=0o755, dir_fd=directory)
                        os.fsync(directory)
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
                info = os.fstat(directory)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError('Protected needrestart configuration directory required')
            name = '90-droiddeck.conf'
            if not inspect:
                H.write_new(directory, name, raw)
            policy = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(policy)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or info.st_mode & 0o022 or info.st_size != len(raw)
                        or os.read(policy, len(raw) + 1) != raw):
                    raise ValueError('Exact protected graph restart policy required')
                if not inspect:
                    os.fchmod(policy, 0o644)
                    os.fsync(policy)
                elif stat.S_IMODE(info.st_mode) != 0o644:
                    raise ValueError('Public root-owned mode0644 restart policy required')
            finally:
                os.close(policy)
            if not inspect:
                os.fsync(directory)
        finally:
            os.close(directory)

    def swap_reserve(self, *, inspect=False):
        reserve = load('host-swap').Reserve(self.source, self.fd)
        reserve.inspect() if inspect else reserve.prepare()

    def verify_runtime(self):
        self.restart_policy(inspect=True)
        self.swap_reserve(inspect=True)
        return self.cli('install-runtime.py', '--verify-runtime', *self.runtime_args(), data=self.raw)

    def units(self):
        # Original registration admits ONLY image/profile preparation state.
        # Publish the fixed GPU recovery tree after the ownership extensions.
        self.cli('stage-installer-sources.py', '--stage-gpu-sources')
        directory = H.protected('/etc/systemd/system', directory=True)
        try:
            for name in ('droiddeck-fresh-gpu-watch@.service', 'droiddeck-fresh-gpu-worker@.service'):
                H.write_new(directory, name, H.protected(self.source / name))
        finally:
            os.close(directory)
        self.run(['/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/droiddeck-fresh-gpu-watch@.service',
                  '/etc/systemd/system/droiddeck-fresh-gpu-worker@.service'])
        self.run(['/usr/bin/systemctl', 'daemon-reload'])

    def defaults(self):
        apps = load('default-apps')
        source = self.work / 'acquired/AuroraStore-4.8.4.apk'
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != apps.SIZE or info.st_nlink != 1:
                raise ValueError('Exact directly acquired Aurora source required')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(apps.SIZE + 1)
            if len(raw) != apps.SIZE or hashlib.sha256(raw).hexdigest() != apps.SHA256:
                raise ValueError('Independent publisher APK pin required')
        finally:
            os.close(fd)
        H.write_new(self.fd, 'AuroraStore.apk', raw)
        args = user_command(self.config, ['/usr/bin/node', self.source / 'prime-installation-media.js'])
        primer = subprocess.Popen(args, stdout=subprocess.PIPE, cwd='/', env=ENV, start_new_session=True)
        try:
            if not select.select([primer.stdout], [], [], 60)[0]:
                raise ValueError('Installation media readiness deadline')
            ready = json.loads(primer.stdout.readline(4096))
            if ready != {'kind': 'installation-media-route', 'active': True, 'tlsVerified': True,
                         'recordingSaved': False, 'androidInputInjected': False}:
                raise ValueError('Actual verified installation media route required')
            media = load('default-media')
            guest = apps.Guest()
            for attempt in range(20):
                try:
                    media.context(guest)
                    break
                except ValueError:
                    if attempt == 19:
                        raise
                    time.sleep(0.5)
            self.cli('installation-defaults.py', '--initialize', STATE / 'AuroraStore.apk')
            self.cli('installation-defaults.py', '--inspect')
        finally:
            try:
                os.killpg(primer.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                primer.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(primer.pid, signal.SIGKILL)
                primer.wait(timeout=10)

    def gpu_preflight(self):
        value = json.loads(self.run(['/usr/bin/node', self.source / 'gpu-service-access.js', 'inspect', STATE / 'configuration.json']))
        if value.get('proposedServiceGpuModeAccess') is not True:
            raise ValueError('Service GPU preflight failed; no Android initialized')

    def verify_gpu(self, token):
        value = json.loads(self.run(['/usr/bin/python3', '-I', '-B', S.GPU_ROOT / 'runtime/portable/fresh-gpu.py', '--inspect', token]))
        if value.get('nativeGpuVerified') is not True or value.get('status', {}).get('stage') != 'ready':
            raise ValueError('Actual ready native GPU rendering required')

    def execute(self):
        user = self.config['user']
        phase = self.phase_run
        phase('01-host', lambda: self.cli('host-prerequisites.py', '--install-host'))
        phase('02-private-directories', self.prepare_directories)
        phase('03-user-session', lambda: self.cli('prepare-user-session.py', '--prepare-user-session', user))
        phase('03a-gpu-preflight', self.gpu_preflight)
        phase('04-acquire-build', lambda: self.cli('acquire-inputs.py', '--acquire-build-inputs', self.work / 'acquired', user=True))
        phase('05-prepare-build', lambda: self.run(user_command(self.config, ['/usr/bin/node', self.source / 'prepare-install.js',
              self.work / 'acquired', self.work / 'prepared'])))
        phase('06-acquire-images', lambda: self.cli('acquire-inputs.py', '--acquire-image-inputs', self.work / 'images', user=True))
        phase('07-extract-images', lambda: self.cli('extract-images.py', self.work / 'images', self.work / 'extracted', user=True))
        phase('08-protect-build', lambda: self.cli('publish-install-inputs.py', '--publish-install-inputs', user, self.work / 'prepared', STATE / 'protected'))
        phase('09-stage-images', lambda: self.cli('stage-images.py', self.work / 'extracted', 'installation'))
        phase('10-promote-images', lambda: self.cli('promote-images.py', 'installation'))
        phase('11-initialize-android', lambda: self.run(['/usr/bin/waydroid', '--details-to-stdout', 'init', '-i', '/etc/waydroid-extra/images', '-s', 'GAPPS']))
        phase('12-software-codecs', lambda: self.cli('promote-images.py', '--codecs'))
        phase('13-cpu-profile', lambda: self.cli('promote-images.py', '--waydroid-baseline'))
        phase('14-protocol-profile', lambda: self.cli('promote-images.py', '--waydroid-protocol-baseline'))
        phase('15-offline-upgrade', lambda: self.run(['/usr/bin/waydroid', 'upgrade', '-o']))
        phase('16-protect-sources', lambda: self.cli('stage-installer-sources.py', '--stage-installer-sources'))
        phase('17-registration', lambda: self.cli('install-registration.py', '--install-registration', user, self.assembly, self.recipes, '-', data=self.raw))
        phase('18-runtime-directories', lambda: self.run(['/usr/bin/systemd-tmpfiles', '--create', '/etc/tmpfiles.d/droiddeck.conf']))
        phase('19-reload-units', lambda: self.run(['/usr/bin/systemctl', 'daemon-reload']))
        phase('20-cpu-session', lambda: self.run(['/usr/bin/systemctl', 'start', 'tesla-waydroid-headless-session.service'], timeout=150))
        phase('21-guest-helpers', lambda: self.cli('runtime-live-guest.py', '--install-new', user, self.assembly, self.recipes, '-', data=self.raw))
        phase('22-stop-cpu-session', lambda: self.run(['/usr/bin/systemctl', 'stop', *UNITS], timeout=210))
        phase('23-runtime-extensions', lambda: self.cli('install-runtime.py', '--resume-runtime', *self.runtime_args(), data=self.raw))
        phase('24-local-tls', lambda: self.cli('setup-local-tls.py', '--initialize-local-tls', user=True))
        phase('25-gpu-units', self.units)
        admission = phase('26-gpu-admission', lambda: self.run(['/usr/bin/python3', '-I', '-B', S.GPU_ROOT / 'runtime/portable/fresh-gpu.py', '--admit', *self.runtime_args()], data=self.raw))
        token = json.loads(admission)['transaction']
        if not re.fullmatch('[a-f0-9]{12}', token):
            raise ValueError('Original GPU admission token required')
        H.write_new(self.fd, 'gpu.json', H.encode({'transaction': token}))
        watcher = 'droiddeck-fresh-gpu-watch@' + token + '.service'
        phase('27-start-watcher', lambda: self.run(['/usr/bin/systemctl', 'start', watcher]))
        phase('28-activate-gpu', lambda: self.run(['/usr/bin/systemctl', 'start', 'droiddeck-fresh-gpu-worker@' + token + '.service'], timeout=210))
        phase('29-enable-watcher', lambda: self.run(['/usr/bin/systemctl', 'enable', watcher]))
        phase('30-start-runtime', lambda: self.run(['/usr/bin/systemctl', 'start', UNITS[0]], timeout=150))
        # Runtime start uses Type=simple; wait for its real HTTPS listener without
        # weakening certificate validation or requiring a human browser gesture.
        phase('31-required-defaults', self.defaults)
        phase('32-wake-initial-display', lambda: self.run(['/usr/bin/lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '--',
              '/system/bin/input', 'keyevent', '224']))
        phase('33-verify-runtime', self.verify_runtime)
        phase('34-verify-gpu', lambda: self.verify_gpu(token))
        H.write_new(self.fd, 'complete.json', H.encode({'kind': 'clean-machine-installation', 'transaction': token,
            'browserRebootRecoveryAcceptanceRequired': True, 'installable': False}))
        return {'kind': 'clean-machine-installation', 'phasesCompleted': 35, 'transaction': token,
                'servicesActivated': True, 'browserTrustConfigured': False, 'releaseAccepted': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 4 or args[0] != '--install-machine' or sys.platform != 'linux' or os.geteuid() != 0 or platform.machine() != 'x86_64':
        raise ValueError('Root Linux --install-machine USER PRIVATE_IPV4 HOSTNAME required')
    if platform.freedesktop_os_release().get('ID') != 'ubuntu' or platform.freedesktop_os_release().get('VERSION_ID') != '26.04':
        raise ValueError('Ubuntu26.04 required')
    package = Path(__file__).absolute().parents[3]
    _files, package_pin = S.read_package(package)
    config = configuration(*args[1:])
    H.clean_target()
    for path in ('/var/lib/droiddeck', '/etc/waydroid-extra', STATE, S.ROOT,
                 Path(config['home']) / 'droiddeck-installation', Path(config['home']) / '.config/droiddeck',
                 Path(config['home']) / '.local/share/waydroid'):
        if os.path.lexists(path):
            raise ValueError('Clean target required; preserve all existing state')
    home = Path(config['home'])
    info = home.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != config['uid'] or info.st_mode & 0o022:
        raise ValueError('Original non-writable selected home required')
    os.umask(0o077)
    STATE.mkdir(mode=0o700)
    fd = H.protected(STATE, directory=True)
    try:
        H.fcntl.flock(fd, H.fcntl.LOCK_EX | H.fcntl.LOCK_NB)
        H.write_new(fd, 'intent.json', H.encode({'schema': 1, 'configuration': config,
                    'packageManifestSha256': package_pin,
                    'machineSha256': S.digest(H.protected('/etc/machine-id'))}))
        H.write_new(fd, 'configuration.json', H.encode(config))
        installer = Install(package, config, fd)
        try:
            print(json.dumps(installer.execute(), sort_keys=True))
        except Exception:
            print('Installation stopped at ' + str(installer.phase) + '; original component recovery is required; never rerun one-time phases', file=sys.stderr)
            raise
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Clean installation incomplete; preserve all state: ' + str(error), file=sys.stderr)
        sys.exit(1)
