# SPDX-License-Identifier: GPL-3.0-only
"""Private two-role payload materialization; no final modes, aliases or activation."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('materialized_install_staging', Path(__file__).with_name('runtime-install-staging.py'))
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)
H, A, E, S = I.H, I.A, I.E, I.S
F, G = H.G.F, H.G


def _hold_snapshot_sources(stack, user, source, recipes, parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    source, recipes, parent = map(A._canonical, (source, recipes, parent))
    if parent == PurePosixPath('/root') or not parent.is_relative_to(PurePosixPath('/root')):
        raise ValueError('Private original candidate parent below /root required')
    roots = source, recipes, parent
    if any(a.is_relative_to(b) or b.is_relative_to(a) for index, a in enumerate(roots) for b in roots[index + 1:]):
        raise ValueError('Disjoint original sources and candidate snapshot required')
    held = E.hold_owned_install_plan(stack, user, source, recipes, raw)
    plan = held['read_plan']()
    records, readers = F._layout(plan)
    manifest = F._manifest(plan, records)
    F._fd_budget(parent, records)
    with ExitStack() as snapshot:
        parent_check = G._hold_parent(snapshot, parent)
        candidate = F._hold_candidate(snapshot, parent / 'candidate', plan)
        held['check']()
        parent_check()
        candidate['check']()
        parent_check()
        held['check']()
    # Only snapshot descriptors are released. Original plan/source/NSS/profile holds remain.
    held['check']()

    def read(name):
        held['check']()
        if name == 'plan.json':
            data = plan
        elif name == A.MANIFEST:
            data = manifest
        elif type(name) is str and name in readers:
            role, original = readers[name]
            data = held['read_source'](original) if role == 'runtime' else held['read_recipe_file'](original)
        else:
            raise ValueError('Closed original source read required')
        expected = records[name] if name in records else (len(manifest), E._sha(manifest))
        if (len(data), E._sha(data)) != expected:
            raise ValueError('Original source differs from independent plan')
        held['check']()
        return data

    return {'check': held['check'], 'read_plan': held['read_plan'], 'read': read, 'policy': {
        **held['policy'], 'kind': 'held-original-install-sources-with-candidate-snapshot',
        'candidateManifestSha256': E._sha(manifest), 'candidateSnapshotVerified': True,
        'candidateSnapshotDescriptorsReleased': True, 'candidateContinuouslyHeld': False,
        'originalSourceContextHeld': True, 'rootPrivateCopyVerified': False}}


def _roles(plan):
    records, _ = F._layout(plan)
    result = {}
    for role, count in (('opt', 229), ('profile', 21)):
        rows = {name[len(role) + 1:]: pair for name, pair in records.items() if name.startswith(role + '/')}
        if len(rows) != count or A.MANIFEST in rows and role == 'opt':
            raise ValueError('Closed independent role layout required')
        # profile already contains its admitted recipe manifest: retain it as a payload.
        if role == 'profile':
            rows = {'recipe/' + name: pair for name, pair in rows.items()}
        manifest = E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'private-install-role-payload',
            'role': role, 'planSha256': E._sha(plan), 'installable': False, 'readyForActivation': False,
            'finalPermissionsApplied': False, 'runtimeLinksMaterialized': False,
            'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(rows.items())]}, compact=True)
        result[role] = (MappingProxyType(rows), manifest)
    return result


def _payload_budget(allocations, roles):
    import resource
    required = len(os.listdir('/proc/self/fd')) + 16
    for allocation, role in zip(allocations, ('opt', 'profile')):
        rows, _ = roles[role]
        groups = {str(p) for name in rows for p in PurePosixPath(name).parents if str(p) != '.'}
        parent = A._canonical(allocation['path'] + '/payload')
        required += 2 * (len(rows) + 1 + len(parent.parts) + 1 + len(groups))
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot retain both role payloads')
    return {'payloadDescriptorLimit': soft, 'payloadDescriptorUpperBound': required}


def materialize_install_payloads(stack, user, source, recipes, parent, raw):
    def prepare(*args):
        return H._prepare_install_intent(*args, _hold_snapshot_sources)

    def materialize(intent, allocations, publish):
        plan = intent['read_plan']()
        roles = _roles(plan)
        budget = _payload_budget(allocations, roles)
        payloads = {}
        for allocation, role in zip(allocations, ('opt', 'profile')):
            rows, manifest = roles[role]
            def read(name, role=role, rows=rows, manifest=manifest):
                if name == A.MANIFEST:
                    return manifest
                if type(name) is not str or name not in rows:
                    raise ValueError('Closed role payload read required')
                original = role + '/' + (name.removeprefix('recipe/') if role == 'profile' else name)
                return intent['read_candidate'](original)
            def verify(s, root, rows=rows, manifest=manifest):
                def pins(actual):
                    if actual != manifest:
                        raise ValueError('Independent role manifest required')
                    return rows
                return A._hold_pinned_tree(s, root, owner=(0, 0), records_reader=pins, manifest_limit=4 * 1024 * 1024)
            result = publish(allocation['target'], {'check': intent['check'], 'read': read,
                'files': (*sorted(rows), A.MANIFEST)}, verify)
            payloads[role] = {**result, 'records': MappingProxyType({**rows, A.MANIFEST: (len(manifest), E._sha(manifest))})}
        return {'payloads': payloads, 'budget': budget}

    result = I._prepare_install_staging(stack, user, source, recipes, parent, raw, prepare, materialize)
    payloads = result['materialized']['payloads']
    def read(role, name):
        if type(role) is not str or role not in payloads:
            raise ValueError('Closed materialized role required')
        result['check']()
        data = payloads[role]['read'](name)
        result['check']()
        return data
    return {**result, 'read_payload': read, 'expectedRecords': MappingProxyType({role: value['records'] for role, value in payloads.items()}),
        'policy': {**result['policy'], 'kind': 'private-materialized-install-payloads',
            'runtimeFilesMaterialized': 229, 'recipeFilesMaterialized': 21, 'roleFilesVerified': 252,
            'rootPrivateCopyVerified': True, 'rootPrivateDependencyCopyVerified': True,
            'rootPrivateArtifactCopyVerified': True, 'finalPermissionsApplied': False,
            'runtimeLinksMaterialized': False, **result['materialized']['budget']}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--materialize-private-payloads' or argv[5] != '-':
        raise ValueError('Only fixed-target private payload materialization is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        result = materialize_install_payloads(stack, argv[1], argv[2], argv[3], argv[4], raw)
        result['check']()
        print(json.dumps(result['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (I.InstallStagingUncertain, H.InstallIntentUncertain):
        print('Private payload materialization uncertain; preserve intent and staging before retry', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Private payload materialization refused; no final publication or recovery performed', file=sys.stderr)
        sys.exit(1)
