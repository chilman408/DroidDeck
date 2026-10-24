# SPDX-License-Identifier: GPL-3.0-only
"""Bounded user-monitor callback proof on the already-running disposable session."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path('/opt/droiddeck/platform-attach-20261014bl')
BASE = Path('/opt/droiddeck/headless-compat-20261014bk2/headless-session.py')
BASE_HASH = 'f9ce747bac5d67a8ce07835c9b8cb66303bf5ef3173a6ed3705ce327fca8664d'
CONFIG = Path('/var/lib/waydroid/waydroid.cfg')
CONFIG_HASH = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'


def load_base():
    for path in [*reversed(BASE.parents), BASE]:
        info = path.lstat()
        if ((info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022 or
                (path != BASE and not stat.S_ISDIR(info.st_mode)) or
                (path == BASE and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise ValueError('Protected attachment dependency required')
    fd = os.open(BASE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        data = os.read(fd, 65537)
        if len(data) > 65536 or hashlib.sha256(data).hexdigest() != BASE_HASH:
            raise ValueError('Attachment dependency pin changed')
    finally:
        os.close(fd)
    module = ModuleType('attach_session_base')
    module.__file__ = str(BASE)
    exec(compile(data, str(BASE), 'exec'), module.__dict__)
    return module


def probe(binding, platform, monitor, drivers, adapt, glib):
    manager = binding.ServiceManager('/dev/anbox-binder', 'aidl6', 'aidl3')
    if not manager.is_present():
        raise ValueError('Platform manager unavailable')
    remote, status = manager.get_service_sync('waydroidusermonitor')
    if status != 0 or remote is not None:
        raise ValueError('Existing or uncertain user monitor; never replace it')
    args = SimpleNamespace(config=str(CONFIG))
    observed = {'callbackReady': False, 'callbacks': 0, 'expired': False}

    def stop():
        loop = getattr(args, 'userMonitorLoop', None)
        if loop is not None: loop.quit()
        return False

    def expire():
        observed['expired'] = True
        return stop()

    def unlocked(uid):
        if type(uid) is int and uid == 0:
            observed['callbacks'] += 1
            observed['callbackReady'] = True
            stop()

    def changed(*ignored):
        # No package names, app lists, accounts or user data are inspected/exported.
        return None

    timeout = glib.timeout_add_seconds(15, expire)
    handlers = []
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            handlers.append(glib.unix_signal_add(glib.PRIORITY_HIGH, number, stop))
        with adapt(platform, monitor, drivers):
            service = platform.get_service(args)
            if (service is None or service.getprop('ro.build.version.sdk', '') != '36' or
                    service.getprop('sys.boot_completed', '') != '1'):
                raise ValueError('Booted SDK36 session required')
            # Existing stock receiver creates its GLib loop and releases local objects.
            # Do not call session.Start, stop Android, or select/change any display mode.
            monitor.add_service(args, unlocked, changed)
    finally:
        if not observed['expired']: glib.source_remove(timeout)
        for handle in handlers: glib.source_remove(handle)
    return {'kind': 'platform-session-attachment', 'sdk': 36,
            'callbackReady': observed['callbackReady'], 'callbacks': observed['callbacks'],
            'probeExpired': observed['expired'], 'sessionRestarted': False,
            'configurationWritten': False, 'propertiesWritten': False,
            'appDataRead': False, 'displayVerified': False, 'installable': False}


def main():
    if (sys.platform != 'linux' or os.getuid() != 1000 or sys.argv[1:] != ['--probe'] or
            Path(__file__) != ROOT / 'platform-session-attach.py'):
        raise ValueError('Fixed unprivileged attachment proof required')
    base = load_base()
    base.protected_bytes(Path(__file__))
    config = base.protected_bytes(CONFIG)
    if hashlib.sha256(config).hexdigest() != CONFIG_HASH:
        raise ValueError('Guarded profile changed')
    adapter = base.load_compat()
    sys.path.insert(0, '/usr/lib/waydroid')
    import gbinder
    from gi.repository import GLib
    from tools.interfaces import IPlatform, IUserMonitor
    from tools.helpers import drivers
    result = probe(gbinder, IPlatform, IUserMonitor, drivers, adapter.adapt_host, GLib)
    if base.protected_bytes(CONFIG) != config:
        raise ValueError('Attachment profile changed')
    print(json.dumps(result, sort_keys=True))
    if not result['callbackReady']: sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Scoped platform attachment unavailable; no session replacement attempted', file=sys.stderr)
        sys.exit(1)
