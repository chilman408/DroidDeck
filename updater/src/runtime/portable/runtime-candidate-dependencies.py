# SPDX-License-Identifier: GPL-3.0-only
"""Read-only coordinated source and fixed dependency admission; no package execution."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from types import MappingProxyType

_spec = importlib.util.spec_from_file_location('runtime_input_admission', Path(__file__).with_name('runtime-candidate-admission.py'))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
POLICIES = MappingProxyType({
    'gateway': MappingProxyType({
        'directory': 'preview-gateway', 'count': 118,
        'bundle': '70c5281fd6333cee014fd66de0f604ed758bfb90df46e23af729697f4b52d600',
        'runtimeManifestSha256': 'b81858425ecda17351ae3a16173e9383b3dcd9eff61dd0b8000a729b4507bd3b',
        'runtimeLockSha256': '6dcd7347aa12def63cd943a6336cb0648c657e72fea911e7cc7f8716b94c794f',
        'manifestSha256': '8fcaef9f5a9e9a0391b04a831aae75269f495a9eac662268d591891081a1af3f',
        'lockSha256': 'f901bd3bbde7880f468e1e02df179551d84f041dba0fe0789c196e63ec26560f',
        'filesSha256': '36a31e5d8046a012b1ff78716102347144af36523f4463db2669e8122b554d98'}),
    'display': MappingProxyType({
        'directory': 'waydroid-display', 'count': 27,
        'bundle': '32bc31152453ff27c8bd65f103b450fa859c2f197e21640e58905d578cc91bad',
        'runtimeManifestSha256': 'dadda34469dc81a6c38eaba5a633a336e266ca6fca38d6a87cdefdd05dce992a',
        'runtimeLockSha256': 'e85d82dc2276d9367edfb2273d7d2fc58c656d651c823d858b93f80c07f6d41f',
        'manifestSha256': '411ec4535eb1eda92b0218d735b02aaca8210adb15936775bfdd5b0334fc3337',
        'lockSha256': 'b694e109e850916294c12fd0850f69f0a0733e9d40b287e8b7380d0c3506e679',
        'filesSha256': 'b4a6884fea6fd759584daeefa1316d25e9ea539b0a0f6be0bd04610fa6a3be88'})})


def dependency_records(role, raw):
    if (type(role) is not str or role not in POLICIES or type(raw) is not bytes or not 0 < len(raw) <= 1048576
            or hashlib.sha256(raw).hexdigest() != POLICIES[role]['bundle']):
        raise ValueError('Independently pinned dependency role manifest required')
    policy, value = POLICIES[role], json.loads(raw)
    if (set(value) != {'version', 'product', 'kind', 'installable', 'role', 'manifestSha256', 'lockSha256', 'filesSha256', 'files'}
            or value['version'] != 1 or value['product'] != 'DroidDeck' or value['kind'] != 'runtime-dependency-component'
            or value['installable'] is not False or value['role'] != role
            or any(value[key] != policy[key] for key in ('manifestSha256', 'lockSha256', 'filesSha256'))
            or len(value['files']) != policy['count']):
        raise ValueError('Fixed dependency metadata required')
    records, folded, total = {}, set(), 0
    for row in value['files']:
        name, size, digest = row['path'], row['size'], row['sha256']
        if (set(row) != {'path', 'size', 'sha256'} or type(name) is not str or len(name) > 240
                or name == A.MANIFEST or len(name.split('/')) > 20
                or any(not re.fullmatch('[A-Za-z0-9._-]+', part) or part in ('.', '..') or part.endswith('.') for part in name.split('/'))
                or name.lower() in folded or type(size) is not int or not 0 <= size <= 8 * 1024 * 1024
                or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Closed bounded dependency payload required')
        records[name] = size, digest
        folded.add(name.lower())
        total += size
    if total > 64 * 1024 * 1024 or hashlib.sha256(json.dumps(value['files'], separators=(',', ':')).encode('ascii')).hexdigest() != policy['filesSha256']:
        raise ValueError('Independent dependency inventory pin required')
    return MappingProxyType(records)


def hold_dependency_bundle(stack, role, root, *, owner=(0, 0)):
    if type(role) is not str or role not in POLICIES:
        raise ValueError('Closed dependency role required')
    return A._hold_pinned_tree(stack, root, owner=owner, records_reader=lambda raw: dependency_records(role, raw), manifest_limit=1048576)


def compatible_candidate(read):
    for policy in POLICIES.values():
        for name, pin in (('package.json', 'runtimeManifestSha256'), ('package-lock.json', 'runtimeLockSha256')):
            if hashlib.sha256(read(policy['directory'] + '/' + name)).hexdigest() != policy[pin]:
                raise ValueError('Candidate package/lock differs from reviewed dependency contract')


def hold_runtime_inputs(stack, user, candidate, gateway, display):
    A._root_linux()
    if type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
        raise ValueError('Exact unprivileged dependency source account required')
    roots = tuple(map(A._canonical, (candidate, gateway, display)))
    if any(first.is_relative_to(second) or second.is_relative_to(first) for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise ValueError('Disjoint coordinated input namespaces required')
    account = A._account(user)
    home = PurePosixPath(account[3])
    if any(root == home or not root.is_relative_to(home) for root in roots[1:]):
        raise ValueError('Dependency bundles beneath independently resolved source home required')
    inputs = {'runtime': A.hold_runtime_candidate(stack, roots[0])}
    for role, root in zip(('gateway', 'display'), roots[1:]):
        if A._account(user) != account:
            raise ValueError('Dependency source account changed')
        inputs[role] = hold_dependency_bundle(stack, role, root, owner=account[1:3])

    def check():
        if A._account(user) != account:
            raise ValueError('Dependency source account changed')
        for held in inputs.values():
            held['check']()
        if A._account(user) != account:
            raise ValueError('Dependency source account changed')

    check()
    compatible_candidate(inputs['runtime']['read'])
    check()

    def read(role, relative):
        if type(role) is not str or role not in inputs:
            raise ValueError('Closed coordinated input role required')
        check()
        raw = inputs[role]['read'](relative)
        check()
        return raw

    return {'check': check, 'read': read, 'policy': {
        **inputs['runtime']['policy'], 'kind': 'coordinated-runtime-inputs',
        'coordinatedInputsVerified': True, 'sourceAccountMatched': True,
        'dependencyRolesVerified': 2, 'dependencyFilesVerified': 145,
        'dependencyFilesInstalled': False, 'rootPrivateDependencyCopyVerified': False,
        'dependencyCodeExecuted': False}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5 or argv[0] != '--verify-inputs':
        raise ValueError('Only fixed read-only coordinated input verification is available')
    with ExitStack() as stack:
        held = hold_runtime_inputs(stack, *argv[1:])
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Runtime dependency admission refused; no changes performed', file=sys.stderr)
        sys.exit(1)
