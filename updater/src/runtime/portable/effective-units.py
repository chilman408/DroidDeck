# SPDX-License-Identifier: GPL-3.0-only
"""Read fixed systemd metadata only; never start/reload units or emit command values."""
import hashlib
import json
import os
import sys

UNITS = ('droiddeck-browser-connection.service', 'tesla-waydroid-compositor.service',
         'tesla-waydroid-headless-session.service', 'tesla-waydroid-runtime.service', 'waydroid-container.service')
COMMANDS = ('ExecCondition', 'ExecStartPre', 'ExecStart', 'ExecStartPost', 'ExecReload', 'ExecReloadPost', 'ExecStop', 'ExecStopPost')


def text(value):
    if not isinstance(value, str) or len(value) > 4096 or '\0' in value:
        raise ValueError('Invalid bounded systemd string')
    return str(value)


def strings(values):
    if not isinstance(values, (list, tuple)) or len(values) > 256:
        raise ValueError('Invalid bounded systemd vector')
    result = [text(value) for value in values]
    if len(set(result)) != len(result):
        raise ValueError('Duplicate systemd vector value')
    return sorted(result)


def integer(value):
    if not isinstance(value, int) or isinstance(value, bool) or type(value).__name__ == 'Boolean' or not 0 <= value <= 2**64 - 1:
        raise ValueError('Invalid systemd integer')
    return int(value)


def boolean(value):
    if type(value) is not bool and type(value).__name__ != 'Boolean':
        raise ValueError('Invalid systemd boolean')
    return bool(value)


def digest(value):
    encoded = json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(encoded) > 65536:
        raise ValueError('Systemd command/environment bound exceeded')
    return hashlib.sha256(encoded).hexdigest()


def command_digest(values):
    if not isinstance(values, (list, tuple)) or len(values) > 16:
        raise ValueError('Invalid systemd command vector')
    result = []
    for item in values:
        if not isinstance(item, (list, tuple)) or len(item) != 10 or not isinstance(item[1], (list, tuple)) or not 1 <= len(item[1]) <= 256:
            raise ValueError('Invalid extended systemd command')
        result.append([text(item[0]), [text(arg) for arg in item[1]], strings(item[2])])
    return digest(result)


def environment_files(values):
    if not isinstance(values, (list, tuple)) or len(values) > 256:
        raise ValueError('Invalid systemd environment-file vector')
    result = []
    for item in values:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('Invalid systemd environment-file entry')
        result.append([text(item[0]), boolean(item[1])])
    return digest(result)


def project_record(service, unit):
    result = {key: text(service[key]) for key in ('Type', 'NotifyAccess', 'User', 'Group', 'WorkingDirectory', 'KillMode', 'Restart')}
    result.update({key: integer(service[key]) for key in ('KillSignal', 'TimeoutStopUSec', 'TimeoutStartUSec')})
    result['SupplementaryGroups'] = strings(service['SupplementaryGroups'])
    result['EnvironmentSha256'] = digest(strings(service['Environment']))
    result['EnvironmentFilesSha256'] = environment_files(service['EnvironmentFiles'])
    result.update({key: strings(service[key]) for key in ('PassEnvironment', 'UnsetEnvironment')})
    # Extended properties retain privileged/ignore-failure prefixes; legacy Exec* loses them.
    result.update({name + 'Sha256': command_digest(service[name + 'Ex']) for name in COMMANDS})
    result.update({key: text(unit[key]) for key in ('LoadState', 'FragmentPath')})
    result.update({key: boolean(unit[key]) for key in ('NeedDaemonReload', 'Transient')})
    result.update({key: strings(unit[key]) for key in ('DropInPaths', 'Requires', 'Wants', 'After', 'PartOf')})
    return result


def snapshot():
    import dbus
    bus = dbus.SystemBus(private=True)
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        result = {}
        for name in UNITS:
            # LoadUnit loads metadata for inactive units; it does not start them.
            obj = bus.get_object('org.freedesktop.systemd1', manager.LoadUnit(name, timeout=5))
            properties = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            before = properties.GetAll('org.freedesktop.systemd1.Unit', timeout=5)
            service = properties.GetAll('org.freedesktop.systemd1.Service', timeout=5)
            after = properties.GetAll('org.freedesktop.systemd1.Unit', timeout=5)
            record = project_record(service, before)
            if project_record(service, after) != record:
                raise ValueError('Unit metadata changed during snapshot')
            result[name] = record
        return result
    finally:
        bus.close()


def main(args):
    if args != ['--snapshot'] or sys.platform != 'linux' or os.geteuid() == 0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
        raise ValueError('Fixed unprivileged Linux system-bus snapshot required')
    print(json.dumps(snapshot(), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Effective unit metadata inspection rejected', file=sys.stderr)
        sys.exit(1)
