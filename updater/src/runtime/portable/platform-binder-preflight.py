# SPDX-License-Identifier: GPL-3.0-only
"""Read-only API36 platform discovery; never registers services or writes properties."""
import json
import os
import stat
import sys

DEVICE = '/dev/anbox-binder'
INTERFACE = 'id.waydro.waydroid.IPlatform'


def inspect(binding):
    manager = binding.ServiceManager(DEVICE, 'aidl6', 'aidl3')
    if not manager.is_present():
        raise ValueError('Platform service manager unavailable')
    remote, status = manager.get_service_sync('waydroidplatform')
    if status != 0 or not remote:
        raise ValueError('Platform service unavailable')
    client = binding.Client(remote, INTERFACE)
    request = client.new_request()
    request.append_string16('ro.build.version.sdk')
    request.append_string16('')
    reply, status = client.transact_sync_reply(1, request)
    if status != 0 or not reply:
        raise ValueError('Read-only platform transaction failed')
    reader = reply.init_reader()
    valid, exception = reader.read_int32()
    if not valid or exception != 0 or reader.read_string16() != '36':
        raise ValueError('API36 platform metadata required')
    return {'kind': 'platform-binder-preflight', 'sdk': 36, 'binderProtocol': 'aidl3',
            'serviceManagerProtocol': 'aidl6', 'interface': INTERFACE,
            'platformReadVerified': True, 'propertiesWritten': False,
            'servicesRegistered': False, 'configurationWritten': False,
            'displayVerified': False, 'installable': False}


def main():
    if sys.platform != 'linux' or os.getuid() != 1000 or len(sys.argv) != 1:
        raise ValueError('Fixed unprivileged Linux preflight required')
    before = os.stat(DEVICE)
    if not stat.S_ISCHR(before.st_mode) or before.st_uid != 0 or before.st_gid != 0:
        raise ValueError('Protected Binder device required')
    import gbinder
    result = inspect(gbinder)
    after = os.stat(DEVICE)
    if (before.st_dev, before.st_ino, before.st_rdev, before.st_mode, before.st_uid, before.st_gid) != (
            after.st_dev, after.st_ino, after.st_rdev, after.st_mode, after.st_uid, after.st_gid):
        raise ValueError('Binder device changed')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Read-only API36 platform preflight unavailable', file=sys.stderr)
        sys.exit(1)
