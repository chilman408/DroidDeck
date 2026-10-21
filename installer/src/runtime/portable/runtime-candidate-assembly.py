# SPDX-License-Identifier: GPL-3.0-only
"""Atomic root-private three-component staging; not an installed or runnable runtime."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import MappingProxyType


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D = _load('runtime-candidate-dependencies')
S = _load('runtime-candidate-stage')
A = D.A
ROLES = ('runtime', 'gateway', 'display')
PUBLISHED_NAME = 'assembly'
FALSE_FLAGS = (*A.FALSE_FLAGS, 'dependencyFilesInstalled', 'dependencyCodeExecuted')


def _manifest(components):
    return (json.dumps({'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-input-assembly',
                        'installable': False, 'components': components}, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def assembly_records(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError('Bounded assembly recipe required')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'version', 'product', 'kind', 'installable', 'components'}
            or type(value['version']) is not int or value['version'] != 1 or value['product'] != 'DroidDeck'
            or value['kind'] != 'runtime-input-assembly' or value['installable'] is not False
            or type(value['components']) is not dict or set(value['components']) != set(ROLES)):
        raise ValueError('Closed noninstallable assembly recipe required')
    records = {}
    for role in ROLES:
        text = value['components'][role]
        if type(text) is not str:
            raise ValueError('Exact original component manifest bytes required')
        manifest = text.encode('utf-8')
        pins = A.candidate_records(manifest) if role == 'runtime' else D.dependency_records(role, manifest)
        for name, pin in pins.items():
            records[role + '/' + name] = pin
        records[role + '/' + A.MANIFEST] = len(manifest), hashlib.sha256(manifest).hexdigest()
    if raw != _manifest(value['components']):
        raise ValueError('Canonical independently pinned assembly recipe required')
    return MappingProxyType(records)


def hold_runtime_assembly(stack, root):
    held = A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=assembly_records, manifest_limit=2 * 1024 * 1024)
    D.compatible_candidate(lambda name: held['read']('runtime/' + name))
    held['check']()
    return {**held, 'policy': {
        'kind': 'held-runtime-input-assembly', 'rootPrivateAssemblyVerified': True,
        'sourceCandidateVerified': True, 'payloadSourcesVerified': 76,
        'dependencyRolesVerified': 2, 'dependencyFilesVerified': 145,
        'rootPrivateDependencyCopyVerified': False, **dict.fromkeys(FALSE_FLAGS, False)}}


def publish_runtime_assembly(stack, user, candidate, gateway, display, parent):
    A._root_linux()
    roots = tuple(map(A._canonical, (candidate, gateway, display, parent)))
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise ValueError('Disjoint input and assembly publication namespaces required')
    inputs = D.hold_runtime_inputs(stack, user, *roots[:3])
    raw = _manifest({role: inputs['read'](role, A.MANIFEST).decode('utf-8') for role in ROLES})
    records = assembly_records(raw)

    def read(relative):
        inputs['check']()
        if relative == A.MANIFEST:
            result = raw
        else:
            if type(relative) is not str or relative not in records:
                raise ValueError('Closed assembly source read required')
            role, name = relative.split('/', 1)
            result = inputs['read'](role, name)
        inputs['check']()
        return result

    package = {'check': inputs['check'], 'read': read, 'files': (*sorted(records), A.MANIFEST)}
    result = S._publish_tree(stack, package, roots[3], published=PUBLISHED_NAME,
        rename=lambda fd, attempt: S._rename_tree_new(fd, attempt, PUBLISHED_NAME), verify=hold_runtime_assembly)
    try:
        inputs['check']()
    except Exception as error:
        raise S.CandidatePublicationUncertain('Assembly may exist; input/account changed before completion; preserve and inspect') from error
    return {**result, 'policy': {**result['policy'], 'newCoordinatedAssemblyPublished': True,
        'sourceAccountMatched': True, 'rootPrivateCopyVerified': True, 'rootPrivateDependencyCopyVerified': True}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != '--verify-assembly':
        raise ValueError('Only read-only assembly verification is exposed')
    with ExitStack() as stack:
        held = hold_runtime_assembly(stack, argv[1])
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Runtime assembly verification refused; no changes performed', file=sys.stderr)
        sys.exit(1)
