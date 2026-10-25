# SPDX-License-Identifier: GPL-3.0-only
"""SDK36 session readiness from Android's real user callback; no policy changes."""
import hashlib
import os
from pathlib import Path
import socket
import stat
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path('/opt/droiddeck/headless-compat-20261014bm')
COMPAT = Path('/opt/droiddeck/platform-display-20261014bj/host-platform-compat.py')
COMPAT_HASH = '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2'
CONFIG_HASH = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'


def protected_bytes(path):
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError('Protected session source required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (before.st_uid, before.st_gid) != (0, 0) or before.st_mode & 0o022 or not 0 < before.st_size <= 1048576):
            raise ValueError('Protected session file required')
        data = os.read(fd, before.st_size + 1)
        fields = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns, i.st_mode, i.st_uid, i.st_gid, i.st_nlink)
        if len(data) != before.st_size or any(fields(i) != fields(before) for i in (os.fstat(fd), path.lstat())):
            raise ValueError('Session source changed')
        return data
    finally:
        os.close(fd)


def load_compat():
    data = protected_bytes(COMPAT)
    if hashlib.sha256(data).hexdigest() != COMPAT_HASH:
        raise ValueError('Tested host adapter changed')
    module = ModuleType('headless_host_compat')
    module.__file__ = str(COMPAT)
    exec(compile(data, str(COMPAT), 'exec'), module.__dict__)
    return module


def send_ready(target, connect=socket.socket):
    if target != '/run/systemd/notify':
        raise ValueError('Systemd notification socket required')
    with connect(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(target)
        channel.sendall(b'READY=1\nSTATUS=Android platform user callback ready')


def run_session(tools, session, platform, monitor, drivers, adapt, notify):
    args = SimpleNamespace()
    tools.prep_args(args)
    with adapt(platform, monitor, drivers):
        def service():
            found = platform.get_service(args)
            if found is None or found.getprop('ro.build.version.sdk', '') != '36':
                raise ValueError('SDK36 platform required')
            return found

        ready = False

        def unlocked():
            nonlocal ready
            found = service()
            # Stable publisher transaction2, not unsupported transaction15.
            found.setprop('waydroid.active_apps', 'Waydroid')
            if found.getprop('waydroid.active_apps', '') != 'Waydroid':
                raise ValueError('Android full-UI selection failed')
            if not ready:
                notify()
                ready = True

        session.start(args, unlocked_cb=unlocked, background=False)


def main():
    if (sys.platform != 'linux' or os.getuid() != 1000 or len(sys.argv) != 1 or
            Path(__file__) != ROOT / 'headless-session.py' or
            os.environ.get('XDG_RUNTIME_DIR') != '/run/user/1000' or
            os.environ.get('DBUS_SESSION_BUS_ADDRESS') != 'unix:path=/run/user/1000/bus' or
            os.environ.get('WAYLAND_DISPLAY') != 'wayland-teslaandroid'):
        raise ValueError('Fixed unprivileged headless service required')
    protected_bytes(Path(__file__))
    config = Path('/var/lib/waydroid/waydroid.cfg')
    if hashlib.sha256(protected_bytes(config)).hexdigest() != CONFIG_HASH:
        raise ValueError('Guarded session profile changed')
    adapter = load_compat()
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    from tools.actions import session_manager
    from tools.interfaces import IPlatform, IUserMonitor
    from tools.helpers import drivers
    # Same D-Bus/GLib initialization as the installed CLI; keep its SIGUSR1 stop.
    import dbus.mainloop.glib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.mainloop.glib.threads_init()
    run_session(tools, session_manager, IPlatform, IUserMonitor, drivers,
                adapter.adapt_host, lambda: send_ready(os.environ.get('NOTIFY_SOCKET')))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Guarded headless compatibility startup unavailable', file=sys.stderr)
        sys.exit(1)
