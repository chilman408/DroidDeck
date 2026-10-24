# SPDX-License-Identifier: GPL-3.0-only
"""Prospective peer service contract; read-only, never install/start incomplete units."""
import hashlib
import json
import os
from pathlib import Path
import sys

WATCHER = 'droiddeck-machine-gpu-watcher.service'
WORKER = 'droiddeck-machine-gpu-worker.service'
UNITS = (WATCHER, WORKER)
UNIT_ROOT = Path('/etc/systemd/system')
ENTRY = '/opt/droiddeck/machine-gpu-entry.py'
ENVIRONMENT = ['LANG=C', 'LC_ALL=C', 'PATH=/usr/bin:/bin']


def properties(name):
    if name not in UNITS:
        raise ValueError('Closed machine peer recipe required')
    watcher = name == WATCHER
    command = ['/usr/bin/python3', '-I', '-B', ENTRY, '--watch' if watcher else '--work']
    service = {'Type': 'notify' if watcher else 'exec', 'NotifyAccess': 'main' if watcher else 'none',
        'User': 'root', 'Group': 'root', 'WorkingDirectory': '/', 'KillMode': 'control-group', 'Restart': 'no',
        'KillSignal': 9, 'TimeoutStartUSec': 60000000, 'TimeoutStopUSec': 60000000,
        'SupplementaryGroups': [], 'Environment': list(ENVIRONMENT), 'EnvironmentFiles': [],
        'PassEnvironment': [], 'UnsetEnvironment': [], 'FinalKillSignal': 9, 'RuntimeMaxUSec': 2**64 - 1,
        'UMask': 0o77, 'SendSIGKILL': True, 'NoNewPrivileges': True, 'RemainAfterExit': False,
        'TimeoutStopFailureMode': 'kill', 'StandardOutput': 'journal', 'StandardError': 'journal'}
    for key in ('ExecCondition', 'ExecStartPre', 'ExecStart', 'ExecStartPost', 'ExecReload', 'ExecReloadPost', 'ExecStop', 'ExecStopPost'):
        service[key + 'Ex'] = []
    service['ExecStartEx'] = [(command[0], command, [], 0, 0, 0, 0, 0, 0, 0)]
    unit = {'LoadState': 'loaded', 'FragmentPath': str(UNIT_ROOT / name), 'NeedDaemonReload': False, 'Transient': False,
        'DropInPaths': [], 'Requires': ['system.slice'] + ([] if watcher else [WATCHER]), 'Wants': [],
        'After': ['system.slice', 'systemd-journald.socket'] + ([] if watcher else [WATCHER]), 'PartOf': [],
        'Before': [WORKER] if watcher else [], 'BindsTo': [] if watcher else [WATCHER], 'Conflicts': [],
        'OnFailure': [], 'OnSuccess': [], 'DefaultDependencies': False, 'RefuseManualStop': False}
    return service, unit


def unit_bytes(name):
    service, _unit = properties(name)
    watcher = name == WATCHER
    peers = '' if watcher else ' ' + WATCHER
    text = ('[Unit]\nDescription=DroidDeck machine GPU ' + ('watcher' if watcher else 'worker') + '\n'
        'DefaultDependencies=no\nRequires=system.slice' + peers + '\n'
        'After=system.slice systemd-journald.socket' + peers + '\n')
    if not watcher:
        text += 'BindsTo=' + WATCHER + '\n'
    text += ('\n[Service]\nType=' + service['Type'] + '\nNotifyAccess=' + service['NotifyAccess'] + '\n'
        'User=root\nGroup=root\nWorkingDirectory=/\nSlice=system.slice\n'
        'ExecStart=' + ' '.join(service['ExecStartEx'][0][1]) + '\n'
        'Environment=LANG=C LC_ALL=C PATH=/usr/bin:/bin\n'
        'Restart=no\nKillMode=control-group\nKillSignal=SIGKILL\nFinalKillSignal=SIGKILL\n'
        'SendSIGKILL=yes\nTimeoutStopFailureMode=kill\nTimeoutStartSec=60s\nTimeoutStopSec=60s\n'
        'RuntimeMaxSec=infinity\nUMask=0077\nNoNewPrivileges=yes\nRemainAfterExit=no\n'
        'StandardOutput=journal\nStandardError=journal\n')
    return text.encode('ascii')


def project(projector, service, unit):
    record = projector.project_record(service, unit)
    record.update({key: projector.integer(service[key]) for key in ('FinalKillSignal', 'RuntimeMaxUSec', 'UMask')})
    record.update({key: projector.boolean(service[key]) for key in ('SendSIGKILL', 'NoNewPrivileges', 'RemainAfterExit')})
    record.update({key: projector.text(service[key]) for key in ('TimeoutStopFailureMode', 'StandardOutput', 'StandardError')})
    record.update({key: projector.strings(unit[key]) for key in ('Before', 'BindsTo', 'Conflicts', 'OnFailure', 'OnSuccess')})
    record.update({key: projector.boolean(unit[key]) for key in ('DefaultDependencies', 'RefuseManualStop')})
    return record


def require_policy(projector, records):
    expected = {name: project(projector, *properties(name)) for name in UNITS}
    # Canonical JSON comparison distinguishes bool/int as well as closed keys and values.
    if type(records) is not dict or json.dumps(records, sort_keys=True, separators=(',', ':')) != json.dumps(expected, sort_keys=True, separators=(',', ':')):
        raise ValueError('Effective machine peer recipe differs from independent contract')


def snapshot(projector):
    if 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
        raise ValueError('Default system bus required')
    import dbus
    bus = dbus.SystemBus(private=True)
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        result = {}
        for name in UNITS:
            # GetUnit only: absent prospective units fail, never load/start metadata or jobs.
            obj = bus.get_object('org.freedesktop.systemd1', manager.GetUnit(name, timeout=5))
            props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            def read():
                unit = props.GetAll('org.freedesktop.systemd1.Unit', timeout=5)
                service = props.GetAll('org.freedesktop.systemd1.Service', timeout=5)
                return project(projector, service, unit)
            before, after = read(), read()
            if before != after:
                raise ValueError('Effective peer recipe changed during observation')
            result[name] = before
        return result
    finally:
        bus.close()


def hold_peer_recipe(copier, stack, sources, unit_root=UNIT_ROOT, *, read_effective=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux protected peer recipe required')
    sources['check']()
    recovery, projector = sources['load']('recovery'), sources['load']('units')
    unit_root = Path(copier.canonical(str(unit_root)))
    chain = copier.open_chain(stack, unit_root, root_owned=True)
    directory = chain[-1][1]
    held = {}
    for name in UNITS:
        fd, info, raw = recovery._read_profile_file(copier, stack, directory, name, maximum=16384, mode=0o644)
        if raw != unit_bytes(name):
            raise ValueError('Protected machine peer file differs from fixed recipe')
        held[name] = fd, info, hashlib.sha256(raw).hexdigest()

    def files():
        sources['check']()
        copier.check_chain(chain, root_owned=True)
        for name, (fd, info, digest) in held.items():
            copier.check_image(directory, name, fd, info)
            if copier.digest_fd(fd, info.st_size) != digest:
                raise ValueError('Held peer service file changed')
            copier.check_image(directory, name, fd, info)
            try:
                os.stat(name + '.d', dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('Unexpected peer drop-in namespace must be preserved')

    observation_verified = read_effective is None
    read_effective = snapshot if read_effective is None else read_effective
    def check():
        files()
        require_policy(projector, read_effective(projector))
        files()
    check()
    return {'check': check, 'peerRecipeFilesMatched': True, 'effectivePeerPolicyMatched': True,
        'installedUnitRootMatched': unit_root == UNIT_ROOT, 'effectiveObservationVerified': observation_verified,
        'installedRecipeVerified': False,
        'effectiveGraphVerified': False, 'independentRecoveryArmed': False, 'servicesActivated': False,
        'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}


def hold_policy_worker(copier, stack, sources, unit_root=UNIT_ROOT, *, read_effective=None,
                       observe=None, identify=None, current_boot=None):
    recipe = hold_peer_recipe(copier, stack, sources, unit_root, read_effective=read_effective)
    supervision = sources['load']('supervision')
    peer = supervision.hold_worker_supervision(stack, admit=recipe['check'], observe=observe,
        identify=identify, current_boot=current_boot)
    return {**recipe, **peer, 'componentSourcesVerified': sources['policy']['componentSourcesVerified'],
            'installedSourceRootMatched': sources['policy']['installedRootMatched']}
