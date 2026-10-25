# SPDX-License-Identifier: GPL-3.0-only
"""Signed host prerequisites for a clean Ubuntu target; no Android initialization."""
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys

try:
    import fcntl
except ImportError:
    fcntl = None

STATE = Path('/var/lib/droiddeck-host-prerequisites')
REPO_URL = 'https://repo.waydro.id/'
REPO_SHA = '2cf79f3cc82adb8c235faca0240c1312867afab0221e9aaf7a539a8614a503fd'
REPO_LIST = b'deb [signed-by=/usr/share/keyrings/waydroid.gpg] https://repo.waydro.id/ resolute main\n'
APT_POLICY = (b'APT::Get::AllowUnauthenticated "false";\n'
              b'Acquire::AllowInsecureRepositories "false";\n'
              b'Acquire::AllowDowngradeToInsecureRepositories "false";\n')
BASE = ('ca-certificates', 'curl', 'gnupg', 'nodejs', 'npm', 'build-essential', 'git',
        'patch', 'pkg-config', 'libglib2.0-dev', 'openjdk-17-jdk-headless', 'unzip',
        'mesa-utils', 'weston', 'time')
RUNTIME = ('waydroid', 'python3-dbus', 'python3-gi', 'python3-protobuf', 'lxc',
           'openjdk-17-jre', 'apksigner', 'aapt',
           'dbus-user-session', 'pipewire', 'pipewire-pulse', 'wireplumber',
           'pulseaudio-utils', 'python3-gst-1.0', 'gstreamer1.0-plugins-base',
           'gstreamer1.0-pipewire', 'gstreamer1.0-tools',
           'gstreamer1.0-plugins-good', 'gstreamer1.0-plugins-bad',
           'gstreamer1.0-plugins-ugly', 'gstreamer1.0-libav', 'ffmpeg', 'socat')
PHASES = ('ubuntu-index', 'build-packages', 'waydroid-repository', 'runtime-packages')
FOREIGN = ('/opt/droiddeck', '/etc/droiddeck', '/var/lib/droiddeck-install-intent',
           '/var/lib/waydroid/waydroid.cfg', '/var/lib/waydroid/data',
           '/etc/waydroid-extra/images')
MODES = ('--install-host', '--resume-host', '--inspect-host')


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def protected(path, *, directory=False, maximum=65536, allow_empty=False):
    """Open only a root-owned no-follow chain, including the final inode."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute protected path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    retained = False
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if not last or directory:
                flags |= os.O_DIRECTORY
            other = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = other
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Non-writable root ownership required')
        info = os.fstat(fd)
        if directory:
            retained = True
            return fd
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not (0 if allow_empty else 1) <= info.st_size <= maximum):
            raise ValueError('Bounded original regular file required')
        raw = os.read(fd, maximum + 1)
        if len(raw) != info.st_size:
            raise ValueError('Stable protected bytes required')
        return raw
    finally:
        if not retained:
            os.close(fd)


def write_new(fd, name, raw):
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(out, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(out)
    finally:
        os.close(out)
    os.fsync(fd)


def clean_target():
    if any(os.path.lexists(name) for name in FOREIGN):
        raise ValueError('Clean target required; installed Android/DroidDeck is never adopted')


def environment():
    return {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root',
            'LANG': 'C', 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive',
            'APT_CONFIG': str(STATE / 'apt-policy.conf'), 'SYSTEMD_OFFLINE': '1'}


def run(args, *, capture=False, timeout=900):
    return subprocess.run(args, cwd='/', env=environment(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE if capture else None, timeout=timeout,
                          check=True, preexec_fn=lambda: os.umask(0o022)).stdout


def apt(operation, *packages):
    args = ['/usr/bin/apt-get', '-o', 'Acquire::Retries=2', '-o', 'DPkg::Lock::Timeout=60']
    if operation == 'install':
        args += ['--yes', '--no-install-recommends', '--no-upgrade', '--no-remove']
    elif operation != 'update' or packages:
        raise ValueError('Fixed APT operation required')
    run([*args, operation, *packages])


def repository(fd):
    script = STATE / 'repository.sh'
    if not os.path.lexists(script):
        raw = run(['/usr/bin/curl', '-q', '--fail', '--location', '--noproxy', '*',
                   '--proto', '=https', '--proto-redir', '=https', '--max-filesize', '65536',
                   '--max-time', '60', '--silent', '--show-error', REPO_URL], capture=True, timeout=90)
        # Keep even a rejected publisher response for diagnosis; never execute it.
        write_new(fd, 'repository.sh', raw or b'empty publisher response\n')
    raw = protected(script)
    if hashlib.sha256(raw).hexdigest() != REPO_SHA:
        raise ValueError('Independently pinned repository script required')
    run(['/usr/bin/bash', str(script), 'resolute'])
    verify_repository()


def verify_repository():
    if protected('/etc/apt/sources.list.d/waydroid.list') != REPO_LIST:
        raise ValueError('Exact signed-by publisher repository required')
    protected('/usr/share/keyrings/waydroid.gpg')
    for path in ('/etc/apt/sources.list.d/waydroid.list', '/usr/share/keyrings/waydroid.gpg'):
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o644:
            raise ValueError('Public APT metadata must be root-owned mode0644')


def package_versions():
    packages = BASE + RUNTIME
    raw = run(['/usr/bin/dpkg-query', '-W', '-f=${binary:Package}\t${Status}\t${Version}\n',
               *packages], capture=True, timeout=30)
    if len(raw) > 65536:
        raise ValueError('Bounded package evidence required')
    versions = {}
    for line in raw.decode('ascii').splitlines():
        binary, status, version = line.split('\t')
        name, separator, architecture = binary.partition(':')
        if separator and architecture != 'amd64':
            raise ValueError('Native prerequisite architecture required')
        if name not in packages or name in versions or status != 'install ok installed' or not version:
            raise ValueError('Every required package must be completely installed')
        versions[name] = version
    if set(versions) != set(packages):
        raise ValueError('Complete host prerequisite inventory required')
    return versions


def finish(fd, intent, mode, *, after_phase=None):
    expected = encode(intent)
    if protected(STATE / 'intent.json') != expected or protected(STATE / 'apt-policy.conf') != APT_POLICY:
        raise ValueError('Original machine/source intent and signed APT policy required')
    complete = []
    missing = False
    for name in PHASES:
        path = STATE / (name + '.json')
        if os.path.lexists(path):
            if missing or protected(path) != encode({'phase': name, 'intent': intent}):
                raise ValueError('Original ordered completion records required')
            complete.append(name)
        else:
            missing = True
    if mode == '--inspect-host' and complete != list(PHASES):
        raise ValueError('Host prerequisite setup is incomplete')
    if mode != '--inspect-host':
        clean_target()
        for name in PHASES[len(complete):]:
            print('Host prerequisites: ' + name, file=sys.stderr, flush=True)
            if name == 'ubuntu-index':
                apt('update')
            elif name == 'build-packages':
                apt('install', *BASE)
            elif name == 'waydroid-repository':
                repository(fd)
            else:
                apt('install', *RUNTIME)
            if after_phase:
                after_phase(name)
            write_new(fd, name + '.json', encode({'phase': name, 'intent': intent}))
    verify_repository()
    versions = package_versions()
    return {'kind': 'host-prerequisites', 'packages': versions, 'readOnly': mode == '--inspect-host',
            'signedAptRequired': True, 'androidInitialized': False, 'wholeInstallationVerified': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args not in ([name] for name in MODES):
        raise ValueError('Explicit host prerequisite operation required')
    if sys.platform != 'linux' or os.geteuid() != 0 or platform.machine() != 'x86_64':
        raise ValueError('Root Linux x86_64 required')
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '26.04':
        raise ValueError('Verified Ubuntu26.04 target required')
    source = protected(Path(__file__).absolute())
    intent = {'schema': 1, 'sourceSha256': hashlib.sha256(source).hexdigest(),
              'machineSha256': hashlib.sha256(protected('/etc/machine-id')).hexdigest(),
              'ubuntu': '26.04', 'architecture': 'x86_64'}
    os.umask(0o077)
    if args[0] == '--install-host':
        clean_target()
        for path in ('/etc/apt/sources.list.d/waydroid.list', '/usr/share/keyrings/waydroid.gpg'):
            if os.path.lexists(path):
                raise ValueError('Pre-existing publisher repository refused')
        STATE.mkdir(mode=0o700)
    fd = protected(STATE, directory=True)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError('Root-private original prerequisite directory required')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args[0] == '--install-host':
            write_new(fd, 'intent.json', encode(intent))
            write_new(fd, 'apt-policy.conf', APT_POLICY)
        print(json.dumps(finish(fd, intent, args[0]), sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Host prerequisites incomplete; preserve original state: ' + str(error), file=sys.stderr)
        sys.exit(1)
