# SPDX-License-Identifier: GPL-3.0-only
"""Process-local bridge for the installed Waydroid 1.6.2 platform/user monitor."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

ROOT = Path('/opt/droiddeck/platform-display-20261014bj')
CONFIG = Path('/var/lib/waydroid/waydroid.cfg')
CONFIG_HASH = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'


@contextmanager
def adapt_host(platform, monitor, drivers):
    old_platform = 'lineageos.waydroid.IPlatform'
    old_monitor = 'lineageos.waydroid.IUserMonitor'
    if (platform.INTERFACE != old_platform or monitor.INTERFACE != old_monitor or
            platform.SERVICE_NAME != 'waydroidplatform' or monitor.SERVICE_NAME != 'waydroidusermonitor' or
            platform.TRANSACTION_getprop != 1 or platform.TRANSACTION_setprop != 2 or
            monitor.TRANSACTION_userUnlocked != 1 or monitor.TRANSACTION_packageStateChanged != 2):
        raise ValueError('Installed host interface contract changed')
    load = drivers.loadBinderNodes

    def load_nodes(args):
        load(args)
        if (args.BINDER_DRIVER, args.VNDBINDER_DRIVER, args.HWBINDER_DRIVER,
                args.BINDER_PROTOCOL, args.SERVICE_MANAGER_PROTOCOL) != (
                'anbox-binder', 'anbox-vndbinder', 'anbox-hwbinder', 'aidl3', 'aidl3'):
            raise ValueError('Installed host Binder profile changed')
        args.SERVICE_MANAGER_PROTOCOL = 'aidl6'

    try:
        platform.INTERFACE = 'id.waydro.waydroid.IPlatform'
        monitor.INTERFACE = 'id.waydro.waydroid.IUserMonitor'
        drivers.loadBinderNodes = load_nodes
        yield
    finally:
        drivers.loadBinderNodes = load
        platform.INTERFACE = old_platform
        monitor.INTERFACE = old_monitor


def protected_file(path):
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError('Protected host path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (before.st_uid, before.st_gid) != (0, 0) or before.st_mode & 0o022 or not 0 < before.st_size <= 1048576):
            raise ValueError('Protected host file required')
        data = os.read(fd, before.st_size + 1)
        fields = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns, i.st_mode, i.st_uid, i.st_gid, i.st_nlink)
        if len(data) != before.st_size or any(fields(i) != fields(before) for i in (os.fstat(fd), path.lstat())):
            raise ValueError('Host source changed')
        return hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)


def main():
    if (sys.platform != 'linux' or os.getuid() != 1000 or sys.argv[1:] != ['--inspect'] or
            Path(__file__) != ROOT / 'host-platform-compat.py'):
        raise ValueError('Installed unprivileged host inspection required')
    files = [Path(__file__), CONFIG, *[Path('/usr/lib/waydroid') / name for name in (
        'tools/interfaces/IPlatform.py', 'tools/interfaces/IUserMonitor.py', 'tools/helpers/drivers.py')]]
    before = {str(path): protected_file(path) for path in files}
    if before[str(CONFIG)] != CONFIG_HASH:
        raise ValueError('Guarded GPU profile changed')
    sys.path.insert(0, '/usr/lib/waydroid')
    from tools.interfaces import IPlatform, IUserMonitor
    from tools.helpers import drivers
    for module, path in ((IPlatform, files[2]), (IUserMonitor, files[3]), (drivers, files[4])):
        if Path(module.__file__) != path:
            raise ValueError('Foreign host module')
    args = SimpleNamespace(config=str(CONFIG))
    with adapt_host(IPlatform, IUserMonitor, drivers):
        # Actual installed getter and discovery, not a reimplementation or a mock.
        service = IPlatform.get_service(args)
        if service is None or service.getprop('ro.build.version.sdk', '') != '36':
            raise ValueError('SDK36 stock host read did not verify')
    if before != {str(path): protected_file(path) for path in files}:
        raise ValueError('Host configuration or source changed')
    print(json.dumps({'kind': 'host-platform-compatibility', 'sdk': 36,
        'stockHostReadVerified': True, 'inProcessAdaptationRestored': True,
        'serviceManagerProtocol': 'aidl6', 'binderProtocol': 'aidl3',
        'configurationWritten': False, 'packageWritten': False,
        'servicesRegistered': False, 'sessionRestarted': False,
        'displayVerified': False, 'installable': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Fixed host platform compatibility inspection unavailable', file=sys.stderr)
        sys.exit(1)
