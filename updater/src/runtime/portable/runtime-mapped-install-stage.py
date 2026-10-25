# SPDX-License-Identifier: GPL-3.0-only
"""Fresh private mapped installation payloads, never a live filesystem install."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('mapped_final_layout', Path(__file__).with_name('runtime-final-layout.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
A, F, S, E = K.A, K.F, K.F.S, K.E


def _layout(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 4 * 1024 * 1024:
        raise ValueError('Bounded independently admitted final layout required')
    value = json.loads(raw)
    if (type(value) is not dict or E._encode(value, compact=True) != raw
            or value.get('version') != 1 or type(value.get('version')) is not int
            or value.get('kind') != 'final-install-placement-plan' or value.get('product') != 'DroidDeck'
            or value.get('targetRoots') != list(K.ROOTS) or value.get('installable') is not False
            or value.get('readyForActivation') is not False or len(value['files']) != 259
            or value['registrationTargets'] != sorted(K.REGISTRATIONS.values())):
        raise ValueError('Canonical complete inert final layout required')
    links = [{'path': K.ROOTS[0] + '/' + name, 'target': target, 'uid': 0, 'gid': 0}
             for name, target in sorted(K.L.LINKS.items())]
    if value['links'] != links:
        raise ValueError('Exact unmaterialized library aliases required')
    records, readers = {}, {}
    artifacts = {K.ROOTS[0] + '/' + name: mode for name, mode in K.L.ARTIFACT_LAYOUT.values()}
    registrations = set(K.REGISTRATIONS.values())
    seen = set()
    for row in value['files']:
        if (type(row) is not dict or set(row) != {'path', 'sourceRole', 'stagedPath', 'size', 'sha256', 'mode', 'uid', 'gid', 'registration'}):
            raise ValueError('Closed mapped file row required')
        destination = str(A._canonical(row['path']))
        registered = destination in registrations
        if (not registered and not any(destination.startswith(root + '/') for root in K.ROOTS)
                or row['registration'] is not registered or row['sourceRole'] not in ('opt', 'profile')
                or any(type(row[key]) is not int or row[key] != 0 for key in ('uid', 'gid'))
                or type(row['mode']) is not int or row['mode'] != artifacts.get(destination, 0o644)
                or type(row['size']) is not int or not 0 <= row['size'] <= 128 * 1024 * 1024
                or type(row['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Closed root-owned bounded mapped payload required')
        F._relative(row['stagedPath'])
        name = F._relative(destination[1:])
        if name in records:
            raise ValueError('Unique mapped destination required')
        records[name] = row['size'], row['sha256']
        readers[name] = destination
        if registered:
            seen.add(destination)
    if seen != registrations or not set(artifacts).issubset(readers.values()):
        raise ValueError('All registration copies and native artifacts required')
    records['final-layout.json'] = len(raw), E._sha(raw)
    names = set(records)
    if (sum(size for size, _ in records.values()) > 512 * 1024 * 1024
            or any(str(parent) in names for name in names for parent in PurePosixPath(name).parents)):
        raise ValueError('Bounded disjoint mapped payload topology required')
    return MappingProxyType(records), MappingProxyType(readers)


def _manifest(raw, records):
    return E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'private-mapped-install-candidate',
        'finalLayoutSha256': E._sha(raw), 'installable': False, 'readyForActivation': False,
        'finalPermissionsApplied': False, 'runtimeLinksMaterialized': False, 'servicesRegistered': False,
        'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(records.items())]}, compact=True)


def _hold_candidate(stack, root, raw):
    records, _ = _layout(raw)
    manifest = _manifest(raw, records)
    def read_records(data):
        if data != manifest:
            raise ValueError('Mapped candidate must match original final layout')
        return records
    return A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=read_records, manifest_limit=4 * 1024 * 1024)


def _preflight_parent(parent):
    with ExitStack() as temporary:
        chain, current = [], None
        for index, part in enumerate(parent.parts):
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            temporary.callback(os.close, fd)
            private = index == len(parent.parts) - 1
            identity = S._directory(os.fstat(fd), private)
            chain.append((current, part, fd, private, identity))
            current = fd
        S._entries(current, set())
        for ancestor, name, fd, private, identity in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if S._directory(info, private) != identity:
                    raise ValueError('Original private mapped staging parent required')


def publish_mapped_install_candidate(stack, user, source, recipes, parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    source, recipes, parent = map(A._canonical, (source, recipes, parent))
    if parent == PurePosixPath('/root') or not parent.is_relative_to(PurePosixPath('/root')):
        raise ValueError('New private mapped parent below /root required')
    roots = source, recipes, parent
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise ValueError('Disjoint mapped staging and original source namespaces required')
    _preflight_parent(parent)
    held = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = held['read_plan']()
    records, readers = _layout(layout)
    manifest = _manifest(layout, records)
    budget = F._fd_budget(parent, records)

    def check_source():
        # The original hold guards its immutable layout's upstream generation.
        held['check']()

    def read_source(name):
        check_source()
        if name == A.MANIFEST:
            data = manifest
        elif name == 'final-layout.json':
            data = layout
        elif type(name) is str and name in readers:
            data = held['read_file'](readers[name])
        else:
            raise ValueError('Closed original mapped source read required')
        if name in records and (len(data), E._sha(data)) != records[name]:
            raise ValueError('Mapped source bytes differ from original final layout')
        check_source()
        return data

    # The publisher independently reopens the empty parent before creating any inode.
    package = {'check': check_source, 'read': read_source, 'files': (*sorted(records), A.MANIFEST)}
    result = S._publish_tree(stack, package, parent, published='candidate', rename=S._rename_new,
        verify=lambda s, root: _hold_candidate(s, root, layout))

    def check():
        try:
            check_source()
            result['check']()
            check_source()
        except Exception as error:
            raise S.CandidatePublicationUncertain('Mapped candidate changed; preserve parent before retry') from error

    def read(name):
        try:
            check()
            data = result['read'](name)
            check()
            return data
        except Exception as error:
            raise S.CandidatePublicationUncertain('Mapped candidate read uncertain; preserve parent before retry') from error

    try:
        check()
    except Exception as error:
        raise S.CandidatePublicationUncertain('Mapped candidate may exist; preserve parent before retry') from error
    return {**result, 'check': check, 'read': read, 'policy': {**held['policy'],
        'kind': 'private-mapped-install-candidate', 'candidatePath': str(parent / 'candidate'),
        'newMappedCandidatePublished': True, 'mappedPayloadFilesVerified': 259, 'candidateFilesVerified': 261,
        'privateMappedCopyVerified': True, 'registrationCopiesStaged': 9, 'mappedLayoutSha256': E._sha(layout),
        'candidateManifestSha256': E._sha(manifest), 'parentPreflightContinuouslyHeld': False,
        'publisherParentIndependentlyReadmitted': True,
        **dict.fromkeys(('installable', 'readyForActivation', 'finalPermissionsApplied', 'runtimeLinksMaterialized',
            'finalTargetsPublished', 'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized'), False),
        **budget}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--stage-mapped-install' or argv[5] != '-':
        raise ValueError('Only new private mapped candidate staging is exposed')
    with ExitStack() as stack:
        held = publish_mapped_install_candidate(stack, argv[1], argv[2], argv[3], argv[4], sys.stdin.buffer.read(E.P.C.LIMIT + 1))
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except S.CandidatePublicationUncertain:
        print('Mapped candidate uncertain; preserve parent and inspect before retry', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Mapped candidate staging refused; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
