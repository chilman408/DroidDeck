# SPDX-License-Identifier: GPL-3.0-only
"""Machine-bound entrypoint for the accepted SDK36 user-callback session protocol."""
from contextlib import ExitStack
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType

ROOT = Path('/opt/droiddeck/headless-runtime')
METADATA = Path('/etc/droiddeck/runtime.json')
WAYDROID = Path('/var/lib/waydroid/waydroid.cfg')
FIELDS = ('version', 'user', 'uid', 'gid', 'lanAddress', 'home', 'port', 'hostname')
PINS = {'headless-session.py': '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba',
        'host-platform-compat.py': '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2'}


def protected_read(filename, maximum=65536, *, allow_empty=False):
    filename = Path(filename)
    if not filename.is_absolute() or '..' in filename.parts:
        raise ValueError('Canonical protected session path required')
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        chain = [(Path('/'), fd)]
        current = Path('/')
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
                    raise ValueError('Protected session namespace changed')
        directories()
        leaf = os.open(filename.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stack.callback(os.close, leaf)
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or before.st_mode & 0o022 or not (0 if allow_empty else 1) <= before.st_size <= maximum):
            raise ValueError('Protected session file required')
        data = os.read(leaf, before.st_size + 1)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                                  value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(data) != before.st_size or any(identity(value) != identity(before) for value in
                (os.fstat(leaf), os.stat(filename.name, dir_fd=fd, follow_symlinks=False))):
            raise ValueError('Session file changed')
        directories()
        return data


def strict_json(encoded):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate session metadata field')
            value[key] = item
        return value
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 16384:
        raise ValueError('Invalid session metadata size')
    return json.loads(encoded.decode('utf-8'), object_pairs_hook=unique)


def decode_configuration(encoded, expected):
    value = strict_json(encoded)
    if not isinstance(value, dict) or set(value) != set(FIELDS) or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Invalid bound session configuration')
    if (type(value['version']) is not int or value['version'] != 1
            or any(type(value[key]) is not int or not 1000 <= value[key] <= 2147483647 for key in ('uid', 'gid'))
            or type(value['port']) is not int or not 1024 <= value['port'] <= 65535
            or not isinstance(value['user'], str) or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', value['user']) or value['user'] == 'root'
            or not isinstance(value['home'], str) or not re.fullmatch('/home/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', value['home'])
            or value['home'].endswith('.') or re.fullmatch('/home/(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\\..*)?', value['home'], re.IGNORECASE)
            or not isinstance(value['hostname'], str)
            or not isinstance(value['lanAddress'], str)
            or len(value['hostname']) > 90 or len(value['hostname'].split('.')) < 2
            or re.fullmatch('[0-9]+', value['hostname'].split('.')[-1])
            or any(not re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value['hostname'].split('.'))):
        raise ValueError('Invalid normalized session identity')
    address = ipaddress.IPv4Address(value['lanAddress'])
    if (not any(address in ipaddress.IPv4Network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
            or int(address) % 256 in (0, 255)):
        raise ValueError('Private session LAN endpoint required')
    config = {key: value[key] for key in FIELDS}
    if hashlib.sha256(json.dumps(config, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() != expected:
        raise ValueError('Session configuration generation changed')
    return config


def validate_identity(config, environment, uid, gid, account):
    if ((uid, gid) != (config['uid'], config['gid'])
            or account != (config['user'], config['uid'], config['gid'], config['home'])):
        raise ValueError('Actual session account does not match metadata')
    runtime = '/run/user/' + str(uid)
    expected = {'HOME': config['home'], 'XDG_RUNTIME_DIR': runtime,
                'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus', 'WAYLAND_DISPLAY': 'wayland-teslaandroid'}
    if any(environment.get(key) != value for key, value in expected.items()):
        raise ValueError('Private session desktop environment changed')


def validate_policy(encoded, generation, configuration):
    value = strict_json(encoded)
    if (not isinstance(value, dict) or set(value) != {'version', 'configurationDigest', 'waydroidConfigurationSha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['configurationDigest'] != generation
            or value['waydroidConfigurationSha256'] != hashlib.sha256(configuration).hexdigest()):
        raise ValueError('Session GPU profile or machine generation changed')


def load_protocol(root, *, read=protected_read):
    sources = {}
    for name, pin in PINS.items():
        encoded = read(Path(root) / name).replace(b'\r\n', b'\n')
        if hashlib.sha256(encoded).hexdigest() != pin:
            raise ValueError('Accepted session protocol source changed')
        sources[name] = encoded
    modules = []
    for name, encoded in sources.items():
        module = ModuleType('portable_' + name.replace('-', '_').replace('.', '_'))
        module.__file__ = str(Path(root) / name)
        exec(compile(encoded, module.__file__, 'exec'), module.__dict__)
        modules.append(module)
    return tuple(modules)


def admit(root, *, read=protected_read, environment=None, uid=None, gid=None, lookup=None):
    environment = os.environ if environment is None else environment
    encoded, policy, waydroid = read(METADATA), read(Path(root) / 'policy.json'), read(WAYDROID)
    generation = environment.get('DROIDDECK_CONFIGURATION_SHA256')
    config = decode_configuration(encoded, generation)
    if lookup is None:
        import pwd
        def lookup(user):
            value = pwd.getpwnam(user)
            return value.pw_name, value.pw_uid, value.pw_gid, value.pw_dir
    validate_identity(config, environment, os.getuid() if uid is None else uid,
                      os.getgid() if gid is None else gid, lookup(config['user']))
    validate_policy(policy, generation, waydroid)
    protocol, adapter = load_protocol(root, read=read)
    if any(read(path) != original for path, original in ((METADATA, encoded), (Path(root) / 'policy.json', policy), (WAYDROID, waydroid))):
        raise ValueError('Session admission files changed')
    return protocol, adapter


def admit_host_namespace(root=Path('/usr/lib/waydroid')):
    root = Path(root)
    required = {'tools/__init__.py', 'tools/actions/__init__.py',
                'tools/helpers/__init__.py', 'tools/actions/session_manager.py', 'tools/interfaces/IPlatform.py',
                'tools/interfaces/IUserMonitor.py', 'tools/helpers/drivers.py'}
    # Admit package initializers and transitive imports BEFORE executing any Waydroid code.
    protected_read(root / 'tools/__init__.py', allow_empty=True)
    pending, files, total, count = [root], {}, 0, 0
    while pending:
        directory = pending.pop()
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError('Protected Waydroid namespace required')
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > 4096:
                    raise ValueError('Waydroid namespace exceeds bound')
                path, info = Path(entry.path), entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    data = protected_read(path, maximum=8388608, allow_empty=True)
                    total += len(data)
                    if total > 67108864:
                        raise ValueError('Waydroid source bytes exceed bound')
                    files[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
                else:
                    raise ValueError('Linked or foreign Waydroid namespace entry')
    if not required <= files.keys():
        raise ValueError('Waydroid host entrypoints missing')
    return files


def main(args):
    if sys.platform != 'linux' or args not in ([], ['--inspect']) or os.getuid() != os.geteuid():
        raise ValueError('Unprivileged Linux session entrypoint required')
    filename = Path(__file__)
    protected_read(filename)
    root = filename.parent
    protocol, adapter = admit(root)
    host_sources = admit_host_namespace()
    if args == ['--inspect']:
        print(json.dumps({'kind': 'portable-headless-admission', 'accountValidated': True, 'protocolSourcesVerified': True,
                          'configurationGenerationVerified': True, 'waydroidProfileVerified': True, 'hostNamespaceVerified': True, 'sessionStarted': False,
                          'installable': False, 'readyForActivation': False, 'androidOperationsExecuted': False}, sort_keys=True))
        return
    if filename != ROOT / 'headless-runtime.py' or os.environ.get('NOTIFY_SOCKET') != '/run/systemd/notify':
        raise ValueError('Fixed supervised headless runtime required')
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
        protected_read(expected, allow_empty=True)
    import dbus.mainloop.glib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.mainloop.glib.threads_init()
    admit(root)
    if host_sources != admit_host_namespace():
        raise ValueError('Waydroid host source generation changed')
    protocol.run_session(tools, session_manager, IPlatform, IUserMonitor, drivers,
                         adapter.adapt_host, lambda: protocol.send_ready(os.environ.get('NOTIFY_SOCKET')))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Portable headless admission/startup rejected', file=sys.stderr)
        sys.exit(1)
