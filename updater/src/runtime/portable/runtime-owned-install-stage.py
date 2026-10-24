# SPDX-License-Identifier: GPL-3.0-only
"""Exclusive private materialization of an admitted owned installation plan."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from types import MappingProxyType

_spec = importlib.util.spec_from_file_location('staged_owned_install_plan', Path(__file__).with_name('runtime-owned-install-plan.py'))
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)
A, S = E.A, E.P.C.L.P.S


def _relative(name):
    if (type(name) is not str or not 0 < len(name) <= 512 or '\x00' in name
            or not name.isascii() or '\\' in name or PurePosixPath(name).is_absolute()
            or str(PurePosixPath(name)) != name or any(p in ('.', '..') for p in name.split('/'))):
        raise ValueError('Closed relative staged path required')
    return name


def _layout(plan):
    if type(plan) is not bytes or not 0 < len(plan) <= 4 * 1024 * 1024:
        raise ValueError('Bounded admitted installation plan required')
    value = json.loads(plan)
    if (type(value) is not dict or value.get('kind') != 'owned-install-runtime-artifact-placement-plan' or value.get('targetRoot') != '/opt/droiddeck'
            or value.get('installable') is not False or value.get('readyForActivation') is not False
            or len(value['files']) != 229 or len(value['ownedRecipeFiles']) != 21 or len(value['links']) != 4):
        raise ValueError('Complete inert owned installation plan required')
    records, readers = {}, {}
    for prefix, rows, role in (('opt/', value['files'], 'runtime'), ('profile/', value['ownedRecipeFiles'], 'recipe')):
        for row in rows:
            keys = {'path', 'size', 'sha256', 'source', 'mode'} if role == 'runtime' else {'path', 'size', 'sha256'}
            if type(row) is not dict or set(row) != keys:
                raise ValueError('Closed admitted staged file row required')
            name, size, pin = prefix + _relative(row['path']), row['size'], row['sha256']
            if (name in records or type(size) is not int or not 0 <= size <= 128 * 1024 * 1024
                    or type(pin) is not str or not re.fullmatch('[a-f0-9]{64}', pin)):
                raise ValueError('Unique bounded staged file records required')
            records[name] = size, pin
            readers[name] = role, _relative(row['source']) if role == 'runtime' else row['path']
    if set(name.removeprefix('profile/') for name in records if name.startswith('profile/')) != set(E.RECIPE_FILES):
        raise ValueError('Complete admitted recipe inventory required')
    records['plan.json'] = len(plan), E._sha(plan)
    if len(records) != 251 or sum(size for size, _ in records.values()) > 512 * 1024 * 1024:
        raise ValueError('Closed bounded staging inventory required')
    names = set(records)
    if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent) != '.'):
        raise ValueError('Disjoint staged file and directory names required')
    return MappingProxyType(records), MappingProxyType(readers)


def _manifest(plan, records):
    return E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-candidate',
        'ownedInstallPlanSha256': E._sha(plan), 'installable': False, 'readyForActivation': False,
        'finalPermissionsApplied': False, 'runtimeLinksMaterialized': False,
        'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(records.items())]}, compact=True)


def _hold_candidate(stack, root, plan):
    records, _ = _layout(plan)
    manifest = _manifest(plan, records)

    def records_reader(raw):
        if raw != manifest:
            raise ValueError('Candidate must match independently held installation plan')
        return records

    return A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=records_reader, manifest_limit=4 * 1024 * 1024)


def _fd_budget(parent, records):
    import resource
    groups = {str(p) for name in (*records, A.MANIFEST) for p in PurePosixPath(name).parents if str(p) != '.'}
    one_tree = len(records) + 1 + len(parent.parts) + 1 + len(groups)
    current = len(os.listdir('/proc/self/fd'))
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    # Publisher retains created inodes and an independent final admission concurrently.
    required = current + 2 * one_tree + 8
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot hold verified private publication')
    return {'descriptorLimit': soft, 'descriptorUpperBound': required}


def publish_owned_install_candidate(stack, user, source, recipes, parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    source, recipes, parent = map(A._canonical, (source, recipes, parent))
    if not parent.is_relative_to(PurePosixPath('/root')) or parent == PurePosixPath('/root'):
        raise ValueError('Independently prepared private staging parent below /root required')
    roots = source, recipes, parent
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise ValueError('Disjoint owned input and staging namespaces required')
    held = E.hold_owned_install_plan(stack, user, source, recipes, raw)
    plan = held['read_plan']()
    records, readers = _layout(plan)
    manifest = _manifest(plan, records)
    budget = _fd_budget(parent, records)

    def read(name):
        held['check']()
        if name == A.MANIFEST:
            result = manifest
        elif name == 'plan.json':
            result = plan
        elif type(name) is str and name in readers:
            role, original = readers[name]
            result = held['read_source'](original) if role == 'runtime' else held['read_recipe_file'](original)
        else:
            raise ValueError('Closed original staging read required')
        if name in records and (len(result), E._sha(result)) != records[name]:
            raise ValueError('Original staged bytes differ from admitted plan')
        held['check']()
        return result

    package = {'check': held['check'], 'read': read, 'files': (*sorted(records), A.MANIFEST)}
    result = S._publish_tree(stack, package, parent, published='candidate', rename=S._rename_new,
        verify=lambda s, root: _hold_candidate(s, root, plan))
    try:
        held['check']()
        result['check']()
    except Exception as error:
        raise S.CandidatePublicationUncertain('Owned candidate may exist; preserve parent and inspect before retry') from error

    def check():
        held['check']()
        result['check']()
        held['check']()

    def checked_read(name):
        check()
        data = result['read'](name)
        check()
        return data

    expected = MappingProxyType({**records, A.MANIFEST: (len(manifest), E._sha(manifest))})
    return {**result, 'check': check, 'read': checked_read, 'expectedRecords': expected, 'policy': {
        **held['policy'], 'kind': 'private-owned-install-candidate', 'candidatePath': str(parent / 'candidate'),
        'newOwnedInstallCandidatePublished': True, 'rootPrivateInstallCandidateVerified': True,
        'rootPrivateCopyVerified': True, 'rootPrivateDependencyCopyVerified': True,
        'rootPrivateArtifactCopyVerified': True, 'runtimeFilesStaged': 229, 'recipeFilesStaged': 21,
        'candidateFilesVerified': 252, 'recipeFilesWritten': True, 'finalPermissionsApplied': False,
        'runtimeLinksMaterialized': False, 'candidateManifestSha256': E._sha(manifest), **budget}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--stage-owned-install' or argv[5] != '-':
        raise ValueError('Only inert private candidate staging with bounded settings stdin is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        held = publish_owned_install_candidate(stack, argv[1], argv[2], argv[3], argv[4], raw)
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except S.CandidatePublicationUncertain:
        print('Owned installation candidate uncertain; preserve parent and inspect before retry', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, E.P.configparser.Error):
        print('Owned installation staging refused; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
