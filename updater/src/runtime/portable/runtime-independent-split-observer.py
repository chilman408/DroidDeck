# SPDX-License-Identifier: GPL-3.0-only
"""Fresh-original read-only split-role observation, never publication or adoption."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('split_observer_readers', Path(__file__).with_name('runtime-independent-role-observer.py'))
O = importlib.util.module_from_spec(spec)
spec.loader.exec_module(O)
X = O.T._load('observed_split_paths', 'runtime-split-role-publication.py')
K, E, A, T, J = O.K, O.E, O.A, O.T, O.J


def _budget(roles):
    import resource
    payload = sum(len(tree['directories']) + len(tree['files']) + len(tree['links']) for tree in roles.values())
    required = len(os.listdir('/proc/self/fd')) + payload + 6 + 3 + 1 + 1 + 5 + 8
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing limit cannot retain independent split roles and metadata')
    return {'independentSplitDescriptorLimit': soft, 'independentSplitDescriptorUpperBound': required}


def _allocation(raw, layout, parents):
    if type(raw) is not bytes or not 0 < len(raw) <= J.LIMIT:
        raise ValueError('Bounded split allocation description required')
    value = json.loads(raw.decode('ascii'))
    if (type(value) is not dict or set(value) != {'version', 'kind', 'transaction', 'planSha256', 'parents', 'roles', 'scope', 'recoveryAuthorized'}
            or J._encode(value) != raw or type(value['version']) is not int or value['version'] != 1
            or value['kind'] != 'private-split-publication-allocation'
            or type(value['transaction']) is not str or not re.fullmatch('[a-f0-9]{32}', value['transaction'])
            or value['planSha256'] != E._sha(layout) or value['scope'] != 'private-current-session-only'
            or value['recoveryAuthorized'] is not False
            or type(value['parents']) is not list or len(value['parents']) != 2
            or type(value['roles']) is not list or len(value['roles']) != 2):
        raise ValueError('Closed canonical split allocation matching fresh layout required')

    def node(row, fields, role):
        if (type(row) is not dict or set(row) != fields or row['role'] != role
                or type(row['device']) is not int or row['device'] < 0
                or type(row['inode']) is not int or row['inode'] <= 0):
            raise ValueError('Exact ordered split role and integer identity descriptions required')

    for role, row in zip(X.ROLES, value['parents']):
        node(row, {'role', 'device', 'inode'}, role)
        if (row['device'], row['inode']) != (parents[role].st_dev, parents[role].st_ino):
            raise ValueError('Split parent description differs from observed namespace')
    X._different_devices(tuple(parents[role].st_dev for role in X.ROLES))
    result = {}
    for role, row in zip(X.ROLES, value['roles']):
        node(row, {'role', 'createdName', 'device', 'inode'}, role)
        if (row['createdName'] != '.' + role + '-attempt-' + value['transaction']
                or row['device'] != parents[role].st_dev):
            raise ValueError('Exact creation name and own-parent device description required')
        result[role] = row['device'], row['inode']
    return result


def hold_independent_split_roles(stack, user, source, recipes, runtime_parent, config_parent, journal_parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    paths = (A._canonical(source), A._canonical(recipes), X._path(runtime_parent, 'runtime'),
             X._path(config_parent, 'config'), J._path(journal_parent))
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('Disjoint fresh originals and private split inspection parents required')
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    roles = T._roles(layout)
    budget = _budget(roles)
    parents = {role: O.P._hold_parent(stack, path) for role, path in zip(X.ROLES, paths[2:4])}

    def parent_check():
        for role, (fd, guard) in parents.items():
            guard()
            O.S._entries(fd, {role, 'allocation.json'} if role == 'runtime' else {role})
        X._different_devices(tuple(os.fstat(parents[role][0]).st_dev for role in X.ROLES))

    parent_check()
    allocation, raw_allocation = O._regular(stack, parents['runtime'][0], 'allocation.json', 0o600)
    described = _allocation(raw_allocation, layout, {role: os.fstat(pair[0]) for role, pair in parents.items()})
    journal_check, progress = O._journal(stack, journal_parent, E._sha(raw_allocation))
    role_checks = [O._role(stack, parents[role][0], role, roles[role], described[role]) for role in X.ROLES]

    def check():
        original['check']()
        parent_check()
        O._read_regular(allocation, allocation['pair'])
        journal_check()
        for role_check in role_checks:
            role_check()
        journal_check()
        parent_check()
        O._read_regular(allocation, allocation['pair'])
        original['check']()

    def observe():
        check()
        return {**original['policy'], **progress, **budget, 'kind': 'independent-private-split-inspection',
            'independentRetainedRoleObserverVerified': True, 'independentSplitRoleObserverVerified': True,
            'readOnlyPrivateInspection': True, 'privateDistinctDevicesObserved': True,
            'privateRegularFilesVerified': 261, 'privateAliasesVerified': 4,
            'finalLayoutSha256': E._sha(layout), 'allocationSha256': E._sha(raw_allocation),
            'configStorageScope': 'ephemeral-private-test-only', 'storedReceiptAdopted': False,
            'createdInodeOwnershipVerified': False, 'privateTargetOwnershipVerified': False,
            'privateCrossDeviceRolePublicationVerified': False, 'crossFilesystemPublicationVerified': False,
            'globallyAtomic': False, 'persistentConfigDestinationVerified': False,
            'concurrentCreatorObserverVerified': False, 'restartRecoveryVerified': False,
            'powerLossRecoveryVerified': False, 'artifactBytesCopied': False,
            **dict.fromkeys(J.FALSE_FLAGS, False)}
    observe()
    return {'check': check, 'observe': observe}


def main(argv=None):
    raise ValueError('No split inspection, publication, adoption or recovery CLI exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No independent-split CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
