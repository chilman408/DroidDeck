# SPDX-License-Identifier: GPL-3.0-only
"""Prepare the selected user's persistent signed PipeWire session, not Android."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

spec = importlib.util.spec_from_file_location('session_host_policy', Path(__file__).with_name('host-prerequisites.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
STATE = Path('/var/lib/droiddeck-user-session')
UNITS = ('pipewire.socket', 'pipewire-pulse.socket', 'wireplumber.service')
MODES = ('--prepare-user-session', '--resume-user-session', '--inspect-user-session')


def account(user):
    import pwd
    if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('Exact selected non-root account required')
    value = pwd.getpwnam(user)
    if (value.pw_uid < 1000 or value.pw_gid < 1000 or value.pw_dir != '/home/' + user
            or value.pw_shell in ('/bin/false', '/usr/sbin/nologin')):
        raise ValueError('Existing regular home account required')
    return {'user': user, 'uid': value.pw_uid, 'gid': value.pw_gid, 'home': value.pw_dir}


def command(args):
    result = subprocess.run(args, cwd='/', stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=60, check=True,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root',
                                 'LANG': 'C', 'LC_ALL': 'C'})
    if len(result.stdout) > 65536 or len(result.stderr) > 65536:
        raise ValueError('Bounded user-session evidence required')
    return result.stdout.decode('ascii').strip()


def user_command(identity, *args):
    uid = str(identity['uid'])
    return command(['/usr/sbin/runuser', '-u', identity['user'], '--', '/usr/bin/env', '-i',
                    'PATH=/usr/bin:/bin', 'HOME=' + identity['home'], 'LANG=C', 'LC_ALL=C',
                    'XDG_RUNTIME_DIR=/run/user/' + uid,
                    'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + uid + '/bus',
                    '/usr/bin/systemctl', '--user', *args])


def verify(identity):
    if account(identity['user']) != identity:
        raise ValueError('Original selected user required')
    if command(['/usr/bin/loginctl', 'show-user', identity['user'], '-p', 'Linger', '--value']) != 'yes':
        raise ValueError('Persistent user manager required')
    for unit in UNITS:
        value = user_command(identity, 'show', unit, '-p', 'ActiveState', '-p', 'UnitFileState',
                             '-p', 'FragmentPath', '-p', 'DropInPaths')
        fields = dict(row.split('=', 1) for row in value.splitlines())
        if fields != {'ActiveState': 'active', 'UnitFileState': 'enabled',
                      'FragmentPath': '/usr/lib/systemd/user/' + unit, 'DropInPaths': ''}:
            raise ValueError('Active enabled signed-distribution user units without overrides required')
        H.protected(fields['FragmentPath'])


def finish(fd, intent, mode, *, after_start=None):
    if H.protected(STATE / 'intent.json') != H.encode(intent):
        raise ValueError('Original user/source session intent required')
    identity = intent['account']
    if account(identity['user']) != identity:
        raise ValueError('Original account generation required')
    complete = STATE / 'complete.json'
    if os.path.lexists(complete):
        if H.protected(complete) != H.encode(intent):
            raise ValueError('Original session completion required')
    elif mode == '--inspect-user-session':
        raise ValueError('User-session preparation incomplete')
    else:
        # These are native idempotent logind/systemd operations, not custom
        # adoption/replacement of application payloads or privileged unit files.
        command(['/usr/bin/loginctl', 'enable-linger', identity['user']])
        command(['/usr/bin/systemctl', 'start', 'user@' + str(identity['uid']) + '.service'])
        user_command(identity, 'daemon-reload')
        user_command(identity, 'enable', '--now', *UNITS)
        if after_start:
            after_start()
        verify(identity)
        H.write_new(fd, 'complete.json', H.encode(intent))
    verify(identity)
    return {'kind': 'persistent-user-media-session', 'account': identity, 'units': list(UNITS),
            'readOnly': mode == '--inspect-user-session', 'androidOperationsExecuted': False,
            'wholeInstallationVerified': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 2 or args[0] not in MODES:
        raise ValueError('Exact user-session operation USER required')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux required')
    identity = account(args[1])
    source = H.protected(Path(__file__).absolute())
    intent = {'schema': 1, 'account': identity,
              'sourceSha256': hashlib.sha256(source).hexdigest(),
              'machineSha256': hashlib.sha256(H.protected('/etc/machine-id')).hexdigest()}
    os.umask(0o077)
    if args[0] == '--prepare-user-session':
        STATE.mkdir(mode=0o700)
    fd = H.protected(STATE, directory=True)
    try:
        if os.fstat(fd).st_mode & 0o777 != 0o700:
            raise ValueError('Root-private original session journal required')
        H.fcntl.flock(fd, H.fcntl.LOCK_EX | H.fcntl.LOCK_NB)
        if args[0] == '--prepare-user-session':
            H.write_new(fd, 'intent.json', H.encode(intent))
        print(json.dumps(finish(fd, intent, args[0]), sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('User-session preparation incomplete; preserve original journal: ' + str(error), file=sys.stderr)
        sys.exit(1)
