# SPDX-License-Identifier: GPL-3.0-only
"""Fixed SDK36 full-UI request; no package patch, profile change or policy write."""
import json
import os
from pathlib import Path
import stat
import sys

DEVICE = '/dev/anbox-binder'
DEVICE_TARGET = '/dev/binderfs/anbox-binder'
INTERFACE = 'id.waydro.waydroid.IPlatform'
ROOT = Path('/opt/droiddeck/platform-display-20261014bj')
# Publisher AIDL declaration order at frameworks_waydroid commit 1c7df400790d10ce.
GETPROP = 1
SHOW_FULL_UI = 15
EXPECTED = {
    'ro.build.version.sdk': '36',
    'sys.boot_completed': '1',
    'waydroid.active_apps': 'Waydroid',
    'ro.waydroid.software_rendering': '0',
    'persist.waydroid.use_subsurface': 'true',
    'debug.stagefright.ccodec': '4',
    'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888',
}
PHASE = 'entry'


class PlatformFailure(ValueError):
    def __init__(self, field, value):
        self.field = field
        self.value = value if type(value) is int and -(2 ** 31) <= value < 2 ** 31 else None
        super().__init__('Platform operation unavailable')


def reply_reader(client, transaction, request):
    reply, status = client.transact_sync_reply(transaction, request)
    if status != 0 or not reply:
        raise PlatformFailure('transport', status)
    reader = reply.init_reader()
    valid, exception = reader.read_int32()
    if not valid or exception != 0:
        raise PlatformFailure('exception' if valid else 'invalid-header', exception)
    return reader


def check_properties(client):
    for key, expected in EXPECTED.items():
        request = client.new_request()
        request.append_string16(key)
        request.append_string16('')
        if reply_reader(client, GETPROP, request).read_string16() != expected:
            raise ValueError('Platform display admission changed')


def request_full_ui(binding, check_device):
    global PHASE
    PHASE = 'device-before'
    check_device()
    PHASE = 'discovery'
    manager = binding.ServiceManager(DEVICE, 'aidl6', 'aidl3')
    if not manager.is_present():
        raise ValueError('Platform service manager unavailable')
    remote, status = manager.get_service_sync('waydroidplatform')
    if status != 0 or not remote:
        raise ValueError('Platform service unavailable')
    client = binding.Client(remote, INTERFACE)
    PHASE = 'admission-before'
    check_properties(client)
    PHASE = 'device-ready'
    check_device()
    # No arguments, generic setters, settings transactions, app launch or callbacks.
    PHASE = 'display-request'
    reply_reader(client, SHOW_FULL_UI, client.new_request())
    PHASE = 'admission-after'
    check_properties(client)
    PHASE = 'device-after'
    check_device()
    return {'kind': 'platform-full-ui-request', 'sdk': 36,
            'serviceManagerProtocol': 'aidl6', 'binderProtocol': 'aidl3',
            'interface': INTERFACE, 'transaction': SHOW_FULL_UI,
            'fullUiRequested': True, 'selectedPropertiesUnchanged': True,
            'configurationWritten': False, 'policyWritten': False,
            'servicesRegistered': False, 'displayVerified': False,
            'mediaPlaybackVerified': False, 'installable': False}


def device_guard():
    link = os.lstat(DEVICE)
    fields = lambda i: (i.st_dev, i.st_ino, i.st_rdev, i.st_mode, i.st_uid, i.st_gid)
    if (not stat.S_ISLNK(link.st_mode) or (link.st_uid, link.st_gid) != (0, 0) or
            os.readlink(DEVICE) != DEVICE_TARGET):
        raise ValueError('Fixed root-owned Binder alias required')
    fd = os.open(DEVICE_TARGET, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISCHR(before.st_mode) or (before.st_uid, before.st_gid) != (0, 0):
            raise ValueError('Protected Binder device required')
        def check():
            if (fields(os.lstat(DEVICE)) != fields(link) or os.readlink(DEVICE) != DEVICE_TARGET or
                    any(fields(i) != fields(before) for i in (os.fstat(fd), os.lstat(DEVICE_TARGET)))):
                raise ValueError('Binder device changed')
        check()
        return fd, check
    except BaseException:
        os.close(fd)
        raise


def main():
    global PHASE
    if (sys.platform != 'linux' or os.getuid() != 1000 or
            sys.argv[1:] != ['--show-full-ui'] or Path(__file__) != ROOT / 'platform-display.py'):
        raise ValueError('Installed unprivileged fixed display request required')
    # No user-writable deployment directory or alternate plugin/import path.
    PHASE = 'installation'
    for path in [*reversed(ROOT.parents), ROOT, Path(__file__)]:
        info = path.lstat()
        if ((info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022 or
                (path != Path(__file__) and not stat.S_ISDIR(info.st_mode)) or
                (path == Path(__file__) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise ValueError('Protected display client required')
    PHASE = 'device-open'
    fd, check = device_guard()
    try:
        PHASE = 'binding-import'
        import gbinder
        print(json.dumps(request_full_ui(gbinder, check), sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        detail = '; ' + error.field + '=' + str(error.value) if isinstance(error, PlatformFailure) else ''
        print('Fixed platform full-UI request unavailable; phase=' + PHASE + detail + '; no picture acceptance', file=sys.stderr)
        sys.exit(1)
