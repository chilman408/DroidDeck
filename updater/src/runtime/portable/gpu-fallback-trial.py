# SPDX-License-Identifier: GPL-3.0-only
"""Explicit disposable-host trial; original GPU sources and admission stay intact."""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


def load(name, root=None):
    source = (root / 'runtime/portable' if root else Path(__file__).parent) / (name + '.py')
    spec = importlib.util.spec_from_file_location('trial_' + name.replace('-', '_'), source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S, G = load('stage-installer-sources'), load('gpu-activation')
H = S.H
BASE = Path('/var/lib/droiddeck/gpu-recovery')
ROOT = Path('/var/lib/droiddeck/gpu-fallback-trials')
SYSTEMD = Path('/etc/systemd/system')
BASE_MANIFEST = '6996c754fe58aa1ce9e62426bd9a60aefbc78089b66e2d65eeec6ca5241c9b20'
WATCH_PIN = '7de2b1af4bfbb29080d797e99e90666369225468d61255db6efbfec972becd0c'
UPDATES = {
    'runtime/portable/fresh-gpu.py': '8f94c96e0af29c60593f80b8f2f5ab3205884d0cefe048129375748167788749',
    'runtime/portable/gpu-activation.py': '17c61c4a0a8e314206c791adaaa64ba4a0771ac093a842045986ad2c1c84b219',
    'runtime/portable/cpu-fallback.js': 'd731566c42bf072dbc3ec1209004c40b8755985d411dd6cffa66fcfce10ba463',
    'runtime/portable/cpu-recovery.py': 'fa69e24ad3652b3d5039bd6edaf0ef164652476cf9be21a9ec00a35a9bb7b7ea',
}
SELF = 'runtime/portable/gpu-fallback-trial.py'
LEGACY_DRIVER = 'f0e4caeda0c688ba890aea8a273057b47dddc5c766918a7387cdcab3a61bf799'
ADMISSIONS = Path('/var/lib/droiddeck/gpu-admissions')
MACHINE = Path('/etc/machine-id')
RETIRE_UNITS = (*G.UNITS, 'droiddeck-browser-connection.service')


def machine_pin():
    mode = stat.S_IMODE(MACHINE.lstat().st_mode)
    if mode not in (0o444, 0o644):
        raise ValueError('Read-only or public machine identity required')
    raw = G.protected_read(MACHINE, mode, 64)
    if not re.fullmatch(b'[a-f0-9]{32}\n?', raw):
        raise ValueError('Exact host machine identity required')
    return G.sha(raw)


def empty_source(path):
    parent = H.protected(path.parent, directory=True)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
                    or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o600)):
                raise ValueError('Original empty source inode required')
            return b''
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def identity(path):
    fd = H.protected(path, directory=True)
    try:
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
            raise ValueError('Original root-private trial directory required')
        return [info.st_dev, info.st_ino]
    finally:
        os.close(fd)


def write_new(path, raw):
    fd = H.protected(path.parent, directory=True)
    try:
        H.write_new(fd, path.name, raw)
    finally:
        os.close(fd)


def mkdir_new(path):
    parent = H.protected(path.parent, directory=True)
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def records(value):
    if type(value) is not list or not 1 <= len(value) <= 1000:
        raise ValueError('Bounded source inventory required')
    result = {}
    total = 0
    for row in value:
        if type(row) is not dict or set(row) != {'path', 'size', 'sha256'}:
            raise ValueError('Closed source record required')
        name = row['path']
        if (type(name) is not str or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}', name)
                or str(PurePosixPath(name)) != name or PurePosixPath(name).is_absolute()
                or any(part in ('.', '..') for part in PurePosixPath(name).parts)
                or name in result or type(row['size']) is not int or not 0 <= row['size'] <= 1048576
                or type(row['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Canonical bounded source record required')
        total += row['size']
        if total > 16 * 1024 * 1024:
            raise ValueError('Bounded source tree required')
        result[name] = row
    return result


def base_sources():
    raw = G.protected_read(BASE / 'intent.json')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'schema', 'packageManifestSha256', 'root', 'machineSha256', 'rootIdentity', 'files'}
            or type(value['schema']) is not int or value['schema'] != 1 or value['root'] != str(BASE)
            or value['packageManifestSha256'] != BASE_MANIFEST or value['rootIdentity'] != identity(BASE)
            or value['machineSha256'] != machine_pin()
            or H.encode(value) != raw or G.protected_read(BASE / 'complete.json') != raw):
        raise ValueError('Original completed RC2 source publication required')
    files = {}
    for name, row in records(value['files']).items():
        content = G.protected_read(BASE / name) if row['size'] else empty_source(BASE / name)
        if len(content) != row['size'] or G.sha(content) != row['sha256']:
            raise ValueError('Original source bytes changed')
        files[name] = content
    fd = H.protected(BASE, directory=True)
    try:
        S.finish(fd, value, files, '--inspect-gpu-sources', root=BASE)
    finally:
        os.close(fd)
    return raw, files


def candidate_files(files, source):
    result = dict(files)
    for name, pin in UPDATES.items():
        raw = G.protected_read(source / name)
        if G.sha(raw) != pin:
            raise ValueError('Exact reviewed fallback source required')
        result[name] = raw
    result[SELF] = G.protected_read(Path(__file__).absolute())
    return result


def layout(token):
    if type(token) is not str or not re.fullmatch('[a-f0-9]{12}', token):
        raise ValueError('Exact original GPU transaction required')
    unit = 'droiddeck-fresh-gpu-watch@' + token + '.service'
    trial = ROOT / token
    return unit, trial, trial / 'sources', SYSTEMD / (unit + '.d') / '90-droiddeck-fallback-trial.conf'


def dropin(token):
    _unit, _trial, source, _path = layout(token)
    return ('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I -B ' + str(source / SELF) +
            ' --watch-trial ' + token + '\nRestart=no\n').encode('ascii')


def unit_layout(unit, expected):
    raw = G.command(['/usr/bin/systemctl', 'show', unit, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
    if len(raw) > 4096:
        raise ValueError('Bounded unit layout required')
    value = dict(line.split('=', 1) for line in raw.decode('ascii').splitlines())
    if value != {'FragmentPath': str(SYSTEMD / 'droiddeck-fresh-gpu-watch@.service'), 'DropInPaths': expected}:
        raise ValueError('Exact original unit and admitted instance override required')


def admission(token):
    raw = G.protected_read(ADMISSIONS / (token + '.json'))
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'version', 'kind', 'transaction', 'configuration',
            'metadataSha256', 'guestReceiptSha256', 'dataDevice', 'dataInode'}
            or type(value['version']) is not int or value['version'] != 1
            or value['kind'] != 'original-owned-fresh-gpu' or value['transaction'] != token):
        raise ValueError('Original immutable GPU admission required')
    return raw, value


def activation(token, source):
    module = load('fresh-gpu', source)
    module.ROOT = source
    module.configure(token)
    _raw, admitted = admission(token)
    policy_source, policy = module.G.sources()
    value = module.Fresh(policy_source, admitted)
    value.metadata()
    return module, value, policy


def template():
    if G.sha(G.protected_read(SYSTEMD / 'droiddeck-fresh-gpu-watch@.service')) != WATCH_PIN:
        raise ValueError('Original root-private watcher template required')


def prepare(token, supplied):
    unit, trial, source, override = layout(token)
    template()
    unit_layout(unit, '')
    old_raw, files = base_sources()
    admit_raw, _value = admission(token)
    original, active, _policy = activation(token, BASE)
    status = active.store.status()
    if status['stage'] != 'ready' or status['boot'] != G.boot_id() or not active.native():
        raise ValueError('Current verified original GPU baseline required')
    G.require_quiet(G.unit(original.G.WORKER))
    watcher = G.unit(unit)
    if watcher['ActiveState'] != 'active' or int(watcher['MainPID']) < 2:
        raise ValueError('Original running watcher required')
    updated = candidate_files(files, Path(supplied))
    if os.path.lexists(trial) or os.path.lexists(override.parent):
        raise ValueError('New trial and instance override directories required; no adoption')
    if not os.path.lexists(ROOT):
        mkdir_new(ROOT)
    identity(ROOT)
    mkdir_new(trial)
    mkdir_new(source)
    mkdir_new(override.parent)
    source_intent = {'schema': 1, 'kind': 'fallback-trial-sources', 'root': str(source),
        'rootIdentity': identity(source), 'baseIntentSha256': G.sha(old_raw),
        'files': [{'path': name, 'size': len(raw), 'sha256': G.sha(raw)} for name, raw in sorted(updated.items())]}
    intent = {'version': 1, 'transaction': token, 'rootIdentity': identity(trial),
        'machineSha256': machine_pin(),
        'baseIntentSha256': G.sha(old_raw), 'admissionSha256': G.sha(admit_raw),
        'sourceIntentSha256': G.sha(H.encode(source_intent)),
        'dropinDirectoryIdentity': identity(override.parent), 'dropinSha256': G.sha(dropin(token)),
        'driverSha256': G.sha(updated[SELF])}
    write_new(trial / 'intent.json', H.encode(intent))
    write_new(source / 'intent.json', H.encode(source_intent))
    fd = H.protected(source, directory=True)
    try:
        S.finish(fd, source_intent, updated, '--stage-gpu-sources', root=source)
    finally:
        os.close(fd)
    write_new(override, dropin(token))
    info = override.lstat()
    write_new(trial / 'complete.json', H.encode({'intentSha256': G.sha(H.encode(intent)),
                                               'dropinIdentity': [info.st_dev, info.st_ino]}))
    return {'kind': 'owned-fallback-trial', 'prepared': True, 'servicesActivated': False}


def inspect(token):
    _unit, trial, source, override = layout(token)
    template()
    old_raw, files = base_sources()
    raw = G.protected_read(trial / 'intent.json')
    intent = json.loads(raw)
    keys = {'version', 'transaction', 'rootIdentity', 'machineSha256', 'baseIntentSha256',
            'admissionSha256', 'sourceIntentSha256', 'dropinDirectoryIdentity', 'dropinSha256', 'driverSha256'}
    if (type(intent) is not dict or set(intent) != keys or type(intent['version']) is not int
            or intent['version'] != 1 or intent['transaction'] != token or raw != H.encode(intent)
            or intent['rootIdentity'] != identity(trial)
            or intent['machineSha256'] != machine_pin()
            or intent['baseIntentSha256'] != G.sha(old_raw)
            or intent['admissionSha256'] != G.sha(admission(token)[0])
            or intent['dropinDirectoryIdentity'] != identity(override.parent)
            or intent['dropinSha256'] != G.sha(dropin(token))
            or G.protected_read(override) != dropin(token)):
        raise ValueError('Original bound trial authority required')
    if any(path.name not in ('intent.json', 'complete.json', 'sources', 'started.json') for path in trial.iterdir()):
        raise ValueError('Only original trial objects allowed')
    done = json.loads(G.protected_read(trial / 'complete.json'))
    info = override.lstat()
    if done != {'intentSha256': G.sha(raw), 'dropinIdentity': [info.st_dev, info.st_ino]}:
        raise ValueError('Original completed trial and override inode required')
    source_raw = G.protected_read(source / 'intent.json')
    if G.sha(source_raw) != intent['sourceIntentSha256']:
        raise ValueError('Original candidate source publication required')
    source_intent = json.loads(source_raw)
    updated = candidate_files(files, source)
    if (G.sha(updated[SELF]) != intent['driverSha256'] or source_intent != {'schema': 1,
            'kind': 'fallback-trial-sources', 'root': str(source), 'rootIdentity': identity(source),
            'baseIntentSha256': G.sha(old_raw), 'files': [{'path': name, 'size': len(content),
                'sha256': G.sha(content)} for name, content in sorted(updated.items())]}):
        raise ValueError('Exact candidate source generation required')
    fd = H.protected(source, directory=True)
    try:
        S.finish(fd, source_intent, updated, '--inspect-gpu-sources', root=source)
    finally:
        os.close(fd)
    return source


def watch(token):
    unit, trial, expected_source, override = layout(token)
    if Path(__file__).absolute() != expected_source / SELF:
        raise ValueError('Fixed admitted candidate entrypoint required')
    source = inspect(token)
    unit_layout(unit, str(override))
    owner = G.unit(unit)
    if owner['ActiveState'] not in ('active', 'activating') or owner['MainPID'] != str(os.getpid()):
        raise ValueError('Original supervised watcher main process required')
    module, active, policy = activation(token, source)
    module.notify()
    started = trial / 'started.json'
    if not os.path.lexists(started):
        with active.store.lock():
            state = active.store.status()
            if state['stage'] != 'ready' or state['boot'] != G.boot_id() or not active.native():
                raise ValueError('First trial must start from current verified GPU')
            write_new(started, H.encode({'version': 1, 'transaction': token, 'boot': G.boot_id()}))
        return active.recover()  # Exactly one supervised original restoration, not activation replay.
    value = json.loads(G.protected_read(started))
    if (type(value) is not dict or set(value) != {'version', 'transaction', 'boot'}
            or type(value['version']) is not int or value['version'] != 1 or value['transaction'] != token
            or type(value['boot']) is not str or not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value['boot'])):
        raise ValueError('Original one-time trial start record required')
    active.resume_boot()
    return active.watch(policy)


def retire(token):
    unit, trial, source, override = layout(token)
    try:
        inspect(token)
    except ValueError:
        # A newer controller cannot silently reinterpret an older admission.
        # Ask only this exact previously tested driver to verify its own tree.
        if G.sha(G.protected_read(source / SELF)) != LEGACY_DRIVER:
            raise
        result = G.command(['/usr/bin/python3', '-I', '-B', str(source / SELF), '--inspect-trial', token], timeout=60)
        if len(result.stdout) > 1024 or json.loads(result.stdout) != {
                'kind': 'owned-fallback-trial', 'readOnly': True, 'verified': True}:
            raise ValueError('Original legacy trial verification required')
    unit_layout(unit, str(override))
    _module, active, _policy = activation(token, BASE)
    if active.store.status()['stage'] != 'restored' or G.sha(G.protected_read(G.WORK / 'waydroid.cfg', 0o644)) != G.CPU:
        raise ValueError('Original restored CPU state required before retiring trial')
    with active.store.lock():
        write_new(trial / 'retire-started.json', H.encode({'version': 1, 'transaction': token}))
    G.command(['/usr/bin/systemctl', 'stop', unit], timeout=210)
    G.require_quiet(G.unit(unit))
    G.command(['/usr/bin/systemctl', 'stop', *RETIRE_UNITS], timeout=100)
    for name in RETIRE_UNITS:
        G.require_quiet(G.unit(name))
    G.command(['/usr/bin/systemctl', 'disable', unit])
    intent = json.loads(G.protected_read(trial / 'intent.json'))
    done = json.loads(G.protected_read(trial / 'complete.json'))
    parent = H.protected(override.parent, directory=True)
    try:
        if identity(override.parent) != intent['dropinDirectoryIdentity'] or list(override.parent.iterdir()) != [override]:
            raise ValueError('Only original trial override may be removed')
        info = override.lstat()
        if [info.st_dev, info.st_ino] != done['dropinIdentity'] or G.protected_read(override) != dropin(token):
            raise ValueError('Original override inode required for retirement')
        os.unlink(override.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    parent = H.protected(SYSTEMD, directory=True)
    try:
        os.rmdir(override.parent.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    G.command(['/usr/bin/systemctl', 'daemon-reload'])
    write_new(trial / 'retired.json', H.encode({'version': 1, 'transaction': token, 'workersStopped': True}))
    return {'kind': 'owned-fallback-trial-retired', 'workersStopped': True, 'gpuActivationReplayed': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if (sys.platform != 'linux' or os.geteuid() != 0 or len(args) not in (2, 3)
            or args[0] not in ('--prepare-trial', '--inspect-trial', '--watch-trial', '--retire-trial')
            or len(args) != (3 if args[0] == '--prepare-trial' else 2)):
        raise ValueError('Explicit root disposable-host trial operation required')
    layout(args[1])
    os.umask(0o077)
    if args[0] == '--prepare-trial':
        result = prepare(args[1], args[2])
    elif args[0] == '--inspect-trial':
        inspect(args[1])
        result = {'kind': 'owned-fallback-trial', 'readOnly': True, 'verified': True}
    elif args[0] == '--retire-trial':
        result = retire(args[1])
    else:
        return watch(args[1])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Fallback trial refused or incomplete: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
