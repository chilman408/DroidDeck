# SPDX-License-Identifier: GPL-3.0-only
"""Admit exactly the derived GPU/software pair without changing the v1 runtime."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType

ROOT = Path('/opt/droiddeck/headless-owned-runtime')
BASE_SHA256 = '2c0c34bf167411556e5a478669a86ec791a73e257cec117c596a0570af368d7b'


def bootstrap_read(filename):
    # Hold and recheck the protected namespace before executing the pinned library.
    filename = Path(filename)
    if not filename.is_absolute() or '..' in filename.parts:
        raise ValueError('Canonical bootstrap path required')
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        chain, current = [(Path('/'), fd)], Path('/')
        for name in filename.parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
            current /= name
            chain.append((current, fd))

        def directories():
            for path, descriptor in chain:
                opened, named = os.fstat(descriptor), path.lstat()
                if (not stat.S_ISDIR(named.st_mode) or (named.st_uid, named.st_gid) != (0, 0)
                        or named.st_mode & 0o022 or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                    raise ValueError('Protected bootstrap namespace changed')
        directories()
        leaf = os.open(filename.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stack.callback(os.close, leaf)
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or before.st_mode & 0o022 or not 1 <= before.st_size <= 32768):
            raise ValueError('Protected bootstrap file required')
        data = os.read(leaf, before.st_size + 1)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                                  value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(data) != before.st_size or any(identity(value) != identity(before) for value in
                (os.fstat(leaf), os.stat(filename.name, dir_fd=fd, follow_symlinks=False))):
            raise ValueError('Bootstrap file changed')
        directories()
        return data


def load_base(root, *, read=None):
    filename = Path(root) / 'headless-runtime.py'
    encoded = (bootstrap_read if read is None else read)(filename).replace(b'\r\n', b'\n')
    if hashlib.sha256(encoded).hexdigest() != BASE_SHA256:
        raise ValueError('Accepted headless library changed')
    module = ModuleType('owned_headless_base')
    module.__file__ = str(filename)
    exec(compile(encoded, module.__file__, 'exec'), module.__dict__)
    return module


def select_profile(encoded, generation, configuration):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate owned policy field')
            value[key] = item
        return value
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 16384:
        raise ValueError('Bounded owned policy required')
    value = json.loads(encoded.decode('utf-8'), object_pairs_hook=unique)
    digest = lambda item: isinstance(item, str) and re.fullmatch('[a-f0-9]{64}', item)
    if (not isinstance(value, dict) or set(value) != {'version', 'configurationDigest', 'originalConfigurationSha256', 'profiles'}
            or type(value['version']) is not int or value['version'] != 2
            or not digest(generation) or value['configurationDigest'] != generation
            or not isinstance(value['profiles'], dict) or set(value['profiles']) != {'software', 'gpu'}):
        raise ValueError('Closed owned profile policy required')
    hashes = [value['originalConfigurationSha256'], *value['profiles'].values()]
    if not all(digest(item) for item in hashes) or len(set(hashes)) != 3:
        raise ValueError('Distinct derived profiles required')
    actual = hashlib.sha256(configuration).hexdigest()
    for name, expected in value['profiles'].items():
        if actual == expected:
            return name
    raise ValueError('Configuration is not an owned session profile')


def admit_owned(root, *, read=None, environment=None, uid=None, gid=None, lookup=None):
    base = load_base(root, read=read)
    reader = base.protected_read if read is None else read
    environment = os.environ if environment is None else environment
    policy_path = Path(root) / 'policy.json'
    policy, configuration = reader(policy_path), reader(base.WAYDROID)
    generation = environment.get('DROIDDECK_CONFIGURATION_SHA256')
    profile = select_profile(policy, generation, configuration)
    configuration_sha = hashlib.sha256(configuration).hexdigest()
    virtual = json.dumps({'version': 1, 'configurationDigest': generation,
                          'waydroidConfigurationSha256': configuration_sha}).encode()

    def bridge(filename):
        encoded = reader(filename)
        if filename == policy_path:
            if encoded != policy:
                raise ValueError('Owned session policy changed')
            return virtual
        return encoded
    protocol, adapter = base.admit(root, read=bridge, environment=environment, uid=uid, gid=gid, lookup=lookup)
    if reader(policy_path) != policy or reader(base.WAYDROID) != configuration:
        raise ValueError('Owned session profile changed during admission')
    return base, protocol, adapter, {'profile': profile, 'configurationSha256': configuration_sha,
                                    'policySha256': hashlib.sha256(policy).hexdigest(), 'configurationDigest': generation}


def main(args):
    if sys.platform != 'linux' or args not in ([], ['--inspect']) or os.getuid() != os.geteuid():
        raise ValueError('Unprivileged Linux owned session entrypoint required')
    filename = Path(__file__)
    root = filename.parent
    base, protocol, adapter, ticket = admit_owned(root)
    base.protected_read(filename)
    host_sources = base.admit_host_namespace()
    if args == ['--inspect']:
        print(json.dumps({'kind': 'portable-owned-headless-admission', 'profile': ticket['profile'],
                          'accountValidated': True, 'protocolSourcesVerified': True, 'configurationGenerationVerified': True,
                          'waydroidProfileVerified': True, 'hostNamespaceVerified': True, 'sessionStarted': False,
                          'installable': False, 'readyForActivation': False, 'androidOperationsExecuted': False}, sort_keys=True))
        return
    if filename != ROOT / 'headless-owned-runtime.py' or os.environ.get('NOTIFY_SOCKET') != '/run/systemd/notify':
        raise ValueError('Fixed supervised owned headless runtime required')
    # Keep orchestration separate so accepted v1 source and its bundle pins stay intact.
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    from tools.actions import session_manager
    from tools.interfaces import IPlatform, IUserMonitor
    from tools.helpers import drivers
    for module, relative in ((tools, 'tools/__init__.py'), (session_manager, 'tools/actions/session_manager.py'),
                             (IPlatform, 'tools/interfaces/IPlatform.py'), (IUserMonitor, 'tools/interfaces/IUserMonitor.py'),
                             (drivers, 'tools/helpers/drivers.py')):
        expected = Path('/usr/lib/waydroid') / relative
        if Path(module.__file__) != expected:
            raise ValueError('Foreign Waydroid host module')
        base.protected_read(expected, allow_empty=True)
    import dbus.mainloop.glib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.mainloop.glib.threads_init()
    if admit_owned(root)[3] != ticket or host_sources != base.admit_host_namespace():
        raise ValueError('Owned session startup generation changed')
    protocol.run_session(tools, session_manager, IPlatform, IUserMonitor, drivers,
                         adapter.adapt_host, lambda: protocol.send_ready(os.environ.get('NOTIFY_SOCKET')))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Owned headless admission/startup rejected', file=sys.stderr)
        sys.exit(1)
