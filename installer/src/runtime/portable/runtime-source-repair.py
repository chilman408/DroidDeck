# SPDX-License-Identifier: GPL-3.0-only
"""Single pinned source-import repair on the disposable portable installation.

No Android state, dependency, TLS, service or publisher image changes. Retains
both manifest generations and supports owned-file rollback without deletion.
"""
from contextlib import ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import types

OLD = 'c9cdeb4d5b2d74216253404336ff6671d3ae390e524e0a173714e5d7b0c8bd4c'
NEW = '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71'
PROBE = '93143ed63e92c2308063cbc57a3894706c8a576ee0dd91d0f381a72a2120bfe7'
PROMOTER = '2b780e865da8f1ff3c8e5f47934ae80a7867ae67227da57003e097ef7dbee1be'
LEAF = 'waydroid-display/probe.js'
NAMESPACE = 'runtime-source-repair'
GROUPS = {'portable', 'browser-connection', 'waydroid-display', 'preview-gateway'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_promoter():
    """Bootstrap only root-held, pinned source; never import a mutable pathname."""
    path = Path(__file__).with_name('promote-images.py')
    with ExitStack() as stack:
        parents, parent = [], None
        for index in range(len(path.parent.parts)):
            current = Path(*path.parent.parts[:index + 1])
            fd = os.open(current.anchor if parent is None else current.name,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsafe repair source ancestor')
            parents.append((current, fd, info.st_dev, info.st_ino))
            parent = fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 1048576
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o600)):
            raise ValueError('Unsafe repair source')
        data = os.read(fd, info.st_size + 1)
        if len(data) != info.st_size or sha(data) != PROMOTER:
            raise ValueError('Repair promoter pin changed')
        module = types.ModuleType('repair_promoter')
        module.__file__ = str(path)
        exec(compile(data, str(path), 'exec'), module.__dict__)
        copier = module.load_copier()
        copier.check_image(parent, path.name, fd, info, private=True)
        copier.check_chain(parents, root_owned=True)
        return module, copier


def guard():
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'tesla-waydroid-runtime.service',
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID'], check=True, timeout=10,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    state = dict(line.split('=', 1) for line in result.stdout.decode('ascii').splitlines())
    if state not in ({'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'},
                      {'ActiveState': 'failed', 'MainPID': '0', 'ControlPID': '0'}):
        raise ValueError('Runtime must be quiescent before source repair')


def rename(source, name, destination, target, exchange=False):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(source, name.encode(), destination, target.encode(), 2 if exchange else 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def transition(p, old, new, probe, pins):
    if (sha(old), sha(new), sha(probe)) != tuple(pins):
        raise ValueError('Closed source repair pins changed')
    before, after = p.component_records(old, runtime=True), p.component_records(new, runtime=True)
    if (len(before) != 75 or len(after) != 76 or LEAF in before or set(after) != {*before, LEAF}
            or any(after[name] != pin for name, pin in before.items()) or after[LEAF] != (len(probe), sha(probe))):
        raise ValueError('Repair must add only the missing import')
    a, b = json.loads(old), json.loads(new)
    a.pop('files'); b.pop('files')
    if a != b or set(name.split('/')[0] for name in before) != GROUPS:
        raise ValueError('Runtime source metadata or groups changed')
    return before


def repair(p, c, root, source, parent, *, restoring=False, check=guard, pins=(OLD, NEW, PROBE)):
    if sys.platform != 'linux' or os.geteuid() != 0 or type(restoring) is not bool:
        raise ValueError('Root Linux closed source repair required')
    root, source, parent = map(Path, (root, source, parent))
    check()
    with ExitStack() as stack:
        chains = [c.open_chain(stack, path, root_owned=True) for path in (root, source, parent)]
        target, origin, receipt_parent = [chain[-1][1] for chain in chains]
        c.protected(os.fstat(target), 0o755)
        c.protected(os.fstat(origin), 0o700)
        group_chains = {name: c.open_chain(stack, root / name, root_owned=True) for name in GROUPS}
        groups = {name: chain[-1][1] for name, chain in group_chains.items()}
        for fd in groups.values():
            c.protected(os.fstat(fd), 0o755)
        new_fd, new_info, new = p._read_profile_file(c, stack, origin, 'bundle-manifest.json')
        probe_fd, probe_info, probe = p._read_profile_file(c, stack, origin, 'probe.js')
        old_fd, old_info, current = p._read_profile_file(c, stack, target, 'bundle-manifest.json', mode=0o644)
        if not restoring:
            before = transition(p, current, new, probe, pins)
        else:
            receipt_chain = c.open_chain(stack, parent / NAMESPACE, root_owned=True)
            receipt = receipt_chain[-1][1]
            c.protected(os.fstat(receipt), 0o700)
            marker_fd, marker_info, marker = p._read_profile_file(c, stack, receipt, 'receipt.json')
            metadata = json.loads(marker)
            exchange_fd, exchange_info, exchanged = p._read_profile_file(c, stack, receipt, 'manifest.exchange', mode=0o644)
            before = transition(p, current if sha(current) == pins[0] else exchanged, new, probe, pins)

        def source_check():
            check()
            for chain in chains + list(group_chains.values()):
                c.check_chain(chain, root_owned=True)
            c.check_image(origin, 'bundle-manifest.json', new_fd, new_info, private=True)
            c.check_image(origin, 'probe.js', probe_fd, probe_info, private=True)
            if c.digest_fd(new_fd, len(new)) != pins[1] or c.digest_fd(probe_fd, len(probe)) != pins[2]:
                raise ValueError('Repair source generation changed')

        def inventory(include_probe):
            source_check()
            if set(os.listdir(target)) != GROUPS | {'bundle-manifest.json'}:
                raise ValueError('Unexpected runtime root entries')
            for name, fd in groups.items():
                expected = {path.split('/')[1] for path in before if path.startswith(name + '/')}
                if name in ('waydroid-display', 'preview-gateway'):
                    expected.add('node_modules')
                    dependency = c.open_chain(stack, root / name / 'node_modules', root_owned=True)
                    c.protected(os.fstat(dependency[-1][1]), 0o755)
                if include_probe and name == 'waydroid-display':
                    expected.add('probe.js')
                if set(os.listdir(fd)) != expected:
                    raise ValueError('Unexpected runtime source inventory')
            for name, (size, pin) in before.items():
                group, leaf = name.split('/')
                with ExitStack() as files:
                    fd, info, data = p._read_profile_file(c, files, groups[group], leaf, mode=0o644)
                    if len(data) != size or sha(data) != pin:
                        raise ValueError('Installed runtime source changed')
                    c.check_image(groups[group], leaf, fd, info)
            source_check()

        if not restoring:
            inventory(False)
            c.check_image(target, 'bundle-manifest.json', old_fd, old_info)
            os.mkdir(NAMESPACE, 0o700, dir_fd=receipt_parent)
            receipt_chain = c.open_chain(stack, parent / NAMESPACE, root_owned=True)
            receipt = receipt_chain[-1][1]
            candidate = c.create_private(stack, receipt, 'probe.ready')
            c.write_all(candidate, probe); os.fchmod(candidate, 0o644); os.fsync(candidate)
            candidate_info = os.fstat(candidate)
            output = c.create_private(stack, receipt, 'manifest.exchange')
            c.write_all(output, new); os.fchmod(output, 0o644); os.fsync(output)
            output_info = os.fstat(output)
            metadata = {'version': 1, 'kind': 'runtime-probe-import-repair',
                'runtimeDevice': os.fstat(target).st_dev, 'runtimeInode': os.fstat(target).st_ino,
                'oldDevice': old_info.st_dev, 'oldInode': old_info.st_ino,
                'newDevice': output_info.st_dev, 'newInode': output_info.st_ino,
                'probeDevice': candidate_info.st_dev, 'probeInode': candidate_info.st_ino,
                'oldSha256': pins[0], 'newSha256': pins[1], 'probeSha256': pins[2]}
            marker_fd = c.create_private(stack, receipt, 'receipt.json')
            marker = (json.dumps(metadata, sort_keys=True) + '\n').encode()
            c.write_all(marker_fd, marker); os.fsync(marker_fd)
            marker_info = os.fstat(marker_fd)
            os.fsync(receipt); os.fsync(receipt_parent)
        expected_keys = {'version', 'kind', 'runtimeDevice', 'runtimeInode', 'oldDevice', 'oldInode',
                         'newDevice', 'newInode', 'probeDevice', 'probeInode', 'oldSha256', 'newSha256', 'probeSha256'}
        if (not isinstance(metadata, dict) or set(metadata) != expected_keys or metadata['version'] != 1
                or type(metadata['version']) is not int or metadata['kind'] != 'runtime-probe-import-repair'
                or tuple(metadata[k] for k in ('oldSha256', 'newSha256', 'probeSha256')) != tuple(pins)
                or any(type(metadata[k]) is not int or metadata[k] < 1 for k in expected_keys if k.endswith(('Device', 'Inode')))
                or (metadata['runtimeDevice'], metadata['runtimeInode']) != (os.fstat(target).st_dev, os.fstat(target).st_ino)):
            raise ValueError('Foreign repair ownership receipt')

        def owned(directory, name, role, pin):
            descriptor, info, data = p._read_profile_file(c, stack, directory, name, mode=0o644)
            if (info.st_dev, info.st_ino) != (metadata[role + 'Device'], metadata[role + 'Inode']) or sha(data) != pin:
                raise ValueError('Repair receipt does not own source generation')
            c.check_image(directory, name, descriptor, info)

        def receipt_check():
            source_check()
            c.check_chain(receipt_chain, root_owned=True)
            c.protected(os.fstat(receipt), 0o700)
            entries = set(os.listdir(receipt))
            if (not {'receipt.json', 'manifest.exchange'} <= entries or
                    entries - {'receipt.json', 'manifest.exchange', 'probe.ready', 'retired-probe.js'} or
                    {'probe.ready', 'retired-probe.js'} <= entries):
                raise ValueError('Unexpected repair receipt inventory')
            c.check_image(receipt, 'receipt.json', marker_fd, marker_info, private=True)
            if c.digest_fd(marker_fd, len(marker)) != sha(marker):
                raise ValueError('Repair ownership receipt changed')

        if restoring:
            locations = [name for name, directory in (('probe.js', groups['waydroid-display']),
                ('probe.ready', receipt), ('retired-probe.js', receipt)) if name in os.listdir(directory)]
            if len(locations) != 1:
                raise ValueError('Owned probe location missing or ambiguous')
            location = locations[0]
            directory = groups['waydroid-display'] if location == 'probe.js' else receipt
            owned(directory, location, 'probe', pins[2])
            inventory(location == 'probe.js')
            if sha(current) == pins[1]:
                if location != 'probe.js':
                    raise ValueError('New manifest without its probe source')
                owned(target, 'bundle-manifest.json', 'new', pins[1])
                owned(receipt, 'manifest.exchange', 'old', pins[0])
                receipt_check()
                rename(receipt, 'manifest.exchange', target, 'bundle-manifest.json', True)
                os.fsync(target); os.fsync(receipt)
            else:
                owned(target, 'bundle-manifest.json', 'old', pins[0])
                owned(receipt, 'manifest.exchange', 'new', pins[1])
            if location != 'retired-probe.js':
                receipt_check()
                owned(directory, location, 'probe', pins[2])
                rename(directory, location, receipt, 'retired-probe.js')
                os.fsync(directory); os.fsync(receipt)
            inventory(False)
            owned(target, 'bundle-manifest.json', 'old', pins[0])
            owned(receipt, 'manifest.exchange', 'new', pins[1])
            owned(receipt, 'retired-probe.js', 'probe', pins[2])
        else:
            receipt_check(); inventory(False)
            owned(target, 'bundle-manifest.json', 'old', pins[0])
            owned(receipt, 'probe.ready', 'probe', pins[2])
            owned(receipt, 'manifest.exchange', 'new', pins[1])
            rename(receipt, 'probe.ready', groups['waydroid-display'], 'probe.js')
            os.fsync(groups['waydroid-display']); os.fsync(receipt)
            receipt_check(); inventory(True)
            owned(groups['waydroid-display'], 'probe.js', 'probe', pins[2])
            owned(target, 'bundle-manifest.json', 'old', pins[0])
            owned(receipt, 'manifest.exchange', 'new', pins[1])
            rename(receipt, 'manifest.exchange', target, 'bundle-manifest.json', True)
            os.fsync(target); os.fsync(receipt)
            inventory(True)
            owned(target, 'bundle-manifest.json', 'new', pins[1])
            owned(receipt, 'manifest.exchange', 'old', pins[0])
        receipt_check()
        return {'kind': metadata['kind'], 'restored': restoring, 'sourceFiles': 75 if restoring else 76,
                'sourceManifestSha256': pins[0] if restoring else pins[1], 'servicesActivated': False,
                'androidDataRead': False, 'installable': False}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or args not in (['--apply'], ['--restore']):
        raise ValueError('Root Linux fixed repair command required')
    p, c = load_promoter()
    result = repair(p, c, Path('/opt/droiddeck/runtime'), Path(__file__).parents[2] / 'component',
                    Path('/var/lib/droiddeck'), restoring=args == ['--restore'])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Source repair refused or uncertain; preserve receipt: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
