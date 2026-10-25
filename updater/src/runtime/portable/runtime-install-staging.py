# SPDX-License-Identifier: GPL-3.0-only
"""Create private same-filesystem staging and original-inode allocation records."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('staging_install_intent', Path(__file__).with_name('runtime-install-intent.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
A, E, S = H.A, H.E, H.S


class InstallStagingUncertain(RuntimeError):
    """An intent or allocation may exist; preserve all state, never blindly retry."""


def _budget(targets):
    import resource
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    required = len(os.listdir('/proc/self/fd')) + sum(len(path.parent.parts) + 3 for path in targets) + 8
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot retain staging ownership evidence')
    return {'stagingDescriptorLimit': soft, 'stagingDescriptorUpperBound': required}


def _new_directory(stack, parent_fd, name):
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    stack.callback(os.close, fd)
    identity = S._directory(os.fstat(fd), True)
    if identity != S._directory(before, True):
        raise ValueError('Original newly allocated staging directory required')
    S._entries(fd, set())
    return fd, identity


def prepare_install_staging(stack, user, source, recipes, candidate_parent, raw):
    return _prepare_install_staging(stack, user, source, recipes, candidate_parent, raw, H.prepare_install_intent)


def _prepare_install_staging(stack, user, source, recipes, candidate_parent, raw, prepare, materialize=None):
    intent = prepare(stack, user, source, recipes, candidate_parent, raw)
    allocations = []
    try:
        original = intent['read_intent']()
        value = json.loads(original)
        if (E._sha(original) != intent['policy']['intentSha256'] or value['kind'] != 'new-install-intent'
                or value['phase'] != 'prepared' or not re.fullmatch('[a-f0-9]{32}', value['transaction'])
                or [row['path'] for row in value['targets']] != list(map(str, H.TARGETS))):
            raise ValueError('Original current-session intent required')
        budget = _budget(H.TARGETS)
        name = '.droiddeck-install-' + value['transaction']

        def check():
            intent['check']()
            for allocation in allocations:
                allocation['parent']['check']()
                for parent, leaf, fd, identity in allocation['directories']:
                    for info in (os.fstat(fd), os.stat(leaf, dir_fd=parent, follow_symlinks=False)):
                        if S._directory(info, True) != identity:
                            raise ValueError('Original allocated staging namespace required')
                S._entries(allocation['outer_fd'], {'payload', 'allocation.json'})
                allocation['payload_guard']()
                fd, before, record = allocation['record']
                for after_read in (False, True):
                    if after_read and A._read(fd, len(record)) != record:
                        raise ValueError('Original allocation record bytes changed')
                    for info in (os.fstat(fd), os.stat('allocation.json', dir_fd=allocation['outer_fd'], follow_symlinks=False)):
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o600)
                                or A._identity(info) != A._identity(before)):
                            raise ValueError('Original private allocation record required')
                allocation['parent']['check']()
            intent['check']()

        for index, path in enumerate(H.TARGETS):
            check()
            parent = H._hold_parent(stack, path.parent)
            info = os.fstat(parent['fd'])
            expected = value['targets'][index]
            if (info.st_dev, info.st_ino) != (expected['parentDevice'], expected['parentInode']):
                raise ValueError('Original intent-bound target parent required')
            check()
            outer, outer_identity = _new_directory(stack, parent['fd'], name)
            payload, payload_identity = _new_directory(stack, outer, 'payload')
            if outer_identity[0] != info.st_dev or payload_identity[0] != info.st_dev:
                raise ValueError('Staging and payload must remain on original target filesystem')
            record = E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'new-install-staging-allocation',
                'transaction': value['transaction'], 'intentSha256': E._sha(original),
                'planSha256': value['planSha256'], 'target': str(path), 'stagingName': name,
                'parentDevice': info.st_dev, 'parentInode': info.st_ino,
                'stagingDevice': outer_identity[0], 'stagingInode': outer_identity[1],
                'payloadDevice': payload_identity[0], 'payloadInode': payload_identity[1],
                'payloadFilesWritten': False, 'targetOwnershipClaimed': False,
                'installationAuthorized': False, 'recoveryAuthorized': False}, compact=True)
            fd, before, _ = S._write_private(stack, outer, 'allocation.json', record)
            allocations.append({'parent': parent, 'outer_fd': outer, 'payload_fd': payload,
                'path': str(path.parent / name), 'target': str(path), 'record': (fd, before, record),
                'payload_guard': lambda fd=payload: S._entries(fd, set()), 'payload_started': False,
                'payload_complete': False,
                'directories': ((parent['fd'], name, outer, outer_identity), (outer, 'payload', payload, payload_identity))})
            check()
            os.fsync(payload)
            os.fsync(outer)
            os.fsync(parent['fd'])
            check()
        check()
        materialized = None
        if materialize is not None:
            def publish(target, package, verify):
                check()
                allocation = next(item for item in allocations if item['target'] == target)
                if allocation['payload_started']:
                    raise ValueError('One exclusive materialization per allocation required')
                allocation['payload_started'] = True
                # The unchanged publisher owns the closed parent inventory during this transition.
                allocation['payload_guard'] = lambda: None
                def guard():
                    package['check']()
                    check()
                result = S._publish_tree(stack, {**package, 'check': guard},
                    A._canonical(allocation['path'] + '/payload'), published='candidate',
                    rename=S._rename_new, verify=verify)
                allocation['payload_guard'] = result['check']
                allocation['payload_complete'] = True
                check()
                return result
            materialized = materialize(intent, tuple(allocations), publish)
            if not all(item['payload_complete'] for item in allocations):
                raise ValueError('Both role payloads must complete before success')
            check()
    except Exception as error:
        raise InstallStagingUncertain('Intent or staging may exist; preserve all paths and inspect before retry') from error

    def retained_check():
        try:
            check()
        except Exception as error:
            raise InstallStagingUncertain('Created staging changed; preserve it and inspect before retry') from error

    def read_allocation(target):
        if type(target) is not str or target not in {item['target'] for item in allocations}:
            raise ValueError('Closed current-session allocation read required')
        retained_check()
        record = next(item['record'][2] for item in allocations if item['target'] == target)
        retained_check()
        return record

    # Descriptors are in-process capabilities, never serialized as recovery authority.
    capabilities = tuple(MappingProxyType({'target': item['target'], 'path': item['path'],
        'parent_fd': item['parent']['fd'], 'outer_fd': item['outer_fd'], 'payload_fd': item['payload_fd']}) for item in allocations)
    return {'check': retained_check, 'read_allocation': read_allocation, 'allocations': capabilities,
        'materialized': materialized,
        'policy': {**intent['policy'], 'kind': 'prepared-private-install-staging',
            'newPrivateStagingAllocations': 2, 'stagingOwnershipRecorded': True,
            'allocationRecordsDurabilityVerified': True, 'stagingPayloadsEmpty': materialize is None,
            'payloadFilesWritten': materialize is not None, 'finalTargetsPublished': False,
            'stagingPaths': [item['path'] for item in allocations], **budget}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--prepare-install-staging' or argv[5] != '-':
        raise ValueError('Only fixed-target private staging preparation is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        result = prepare_install_staging(stack, argv[1], argv[2], argv[3], argv[4], raw)
        result['check']()
        print(json.dumps(result['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (InstallStagingUncertain, H.InstallIntentUncertain):
        print('Installation staging uncertain; preserve intent and staging paths before retry', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Installation staging refused; no publication or recovery performed', file=sys.stderr)
        sys.exit(1)
