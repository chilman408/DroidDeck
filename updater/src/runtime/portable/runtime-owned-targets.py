# SPDX-License-Identifier: GPL-3.0-only
"""Fresh-admitted owned payloads in exclusively NEW targets; no registration CLI."""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = _load('owned_target_payloads', 'runtime-admitted-role-publication.py')
C = _load('owned_target_allocator', 'runtime-target-allocation.py')
X = _load('owned_target_storage', 'runtime-split-role-publication.py')
P = _load('owned_target_host_packages', 'runtime-host-package-files.py')
H = P.N
K, E, A = T.K, T.E, T.A
PREFIXES = {'runtime': 'opt/droiddeck/', 'config': 'etc/droiddeck/'}


def _hold_host_namespaces(stack):
    guards = (P.hold_host_package_files(stack), H.hold_support_namespace(stack, prepared_state=True), H.U.hold_unit_namespace(stack),
              H.R.hold_registration_paths(stack))
    active = True

    def expire():
        nonlocal active
        active = False

    stack.callback(expire)

    def check():
        if not active:
            raise ValueError('Original host namespace context must remain open')
        for guard in guards:
            guard['check']()

    check()
    return check


def _bind_support_account(raw, user):
    E.P.C._bounded(raw)
    value = json.loads(raw)
    if (type(value) is not dict or tuple(value) != E.P.C.FIELDS or E._encode(value) != raw
            or type(value['version']) is not int or value['version'] != 1
            or any(type(value[name]) is not int for name in ('uid', 'gid'))):
        raise ValueError('Fresh canonical account-bound runtime configuration required')
    account = K.L._account(user)
    if tuple(value[name] for name in ('user', 'uid', 'gid', 'home')) != account:
        raise ValueError('Original runtime configuration and target account must match')

    def check():
        if K.L._account(user) != account:
            raise ValueError('Original support account mapping changed')

    check()
    return account[2], check


def _trees(layout):
    original = T.R.Q._spec(layout)
    plan = json.loads(layout)
    registrations = {row['path'][1:] for row in plan['files'] if row['registration']}
    if registrations != {value[1:] for value in K.REGISTRATIONS.values()}:
        raise ValueError('Exact nine separately pending registration targets required')
    trees, selected = {}, set()
    for role, prefix in PREFIXES.items():
        files = {name[len(prefix):]: pair for name, pair in original['files'].items() if name.startswith(prefix)}
        links = {name[len(prefix):]: value for name, value in original['links'].items() if name.startswith(prefix)}
        directories = {'': 0o700}
        for name in (*files, *links):
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.':
                    directories[str(parent)] = original['directories'][prefix + str(parent)]
        selected.update(prefix + name for name in files)
        trees[role] = {'files': files, 'links': links, 'directories': directories}
    if (len(selected) != 250 or selected & registrations
            or selected | registrations != set(original['files']) - {'final-layout.json', A.MANIFEST}
            or sum(len(tree['links']) for tree in trees.values()) != 4):
        raise ValueError('Exact250 owned files/four aliases, excluding registrations and packaging required')
    return trees


def _populate_new_targets(stack, user, source, recipes, raw, parents):
    A._root_linux()
    E.P.C._bounded(raw)
    parents = C._parents(parents)
    sources = (A._canonical(source), A._canonical(recipes))
    targets = tuple(parent / C.NAME for parent in parents)
    if any(a.is_relative_to(b) or b.is_relative_to(a) for a in sources for b in targets):
        raise ValueError('Original sources and owned target roots must be disjoint')
    namespace = _hold_host_namespaces(stack)
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    trees = _trees(layout)
    import resource
    required = len(os.listdir('/proc/self/fd')) + sum(len(parent.parts) for parent in parents) + 2 + 12
    required += sum(len(tree['directories']) - 1 + len(tree['files']) + len(tree['links']) for tree in trees.values())
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot retain original and owned targets')
    if sum(size for tree in trees.values() for size, _, _ in tree['files'].values()) > 512 * 1024 * 1024:
        raise ValueError('Bounded owned target payload memory required')
    payloads = {}
    for role, prefix in PREFIXES.items():
        payloads[role] = {}
        for name, (size, pin, _) in trees[role]['files'].items():
            original['check']()
            data = original['read_file']('/' + prefix + name)
            if type(data) is not bytes or (len(data), E._sha(data)) != (size, pin):
                raise ValueError('Fresh admitted owned payload bytes required')
            payloads[role][name] = data
    original['check']()
    gid, account_check = _bind_support_account(payloads['config']['runtime.json'], user)
    H._validate_support_recipes(payloads['config']['services/pam.d/droiddeck'],
        payloads['config']['services/tmpfiles.d/droiddeck.conf'], expected_gid=gid)
    storage = {}

    def check_inputs():
        namespace()
        account_check()
        original['check']()

    def before_create(fds):
        check_inputs()
        plan = {role: {'sizes': tuple(size for size, _, _ in tree['files'].values()),
            'directories': len(tree['directories']), 'links': len(tree['links'])} for role, tree in trees.items()}
        # Reuse conservative allocation/full-journal allowance, without writing either.
        storage.update(X._storage_preflight({**dict(fds), 'journal': fds['runtime']}, plan, private_parents=False))
        check_inputs()
    owned = C._allocate_targets(stack, tuple(map(str, parents)), E._sha(layout), check_inputs, before_create=before_create)
    built, poisoned = {}, False
    try:
        for role in C.ROLES:
            check_inputs()
            owned['check']()
            built[role] = T._build_role(stack, owned['directory_fds'][role], trees[role], payloads[role], original,
                metadata_name=None)
            built[role]['sync']()
            owned['check']()
            check_inputs()
    except Exception as error:
        raise C.AllocationUncertain('Partial owned payloads possible; preserve both targets, no adoption/retry') from error

    def observe():
        nonlocal poisoned
        if poisoned:
            raise C.AllocationUncertain('Owned payload context poisoned; preserve both targets')
        try:
            check_inputs()
            report = owned['observe']()
            for role in C.ROLES:
                built[role]['check']()
            check_inputs()
            owned['check']()
            return {**report, 'kind': 'held-new-owned-target-payloads', 'ownedPayloadFilesVerified': 250,
                'ownedLibraryAliasesVerified': 4, 'ownedTargetPayloadsVerified': True,
                'filesWritten': True, 'registrationsWritten': 0, 'privatePackagingInstalled': False,
                'ownedTargetDescriptorLimit': soft, 'ownedTargetDescriptorUpperBound': required,
                'storageCapacityObserved': storage['privateParentSpacePreflightVerified'],
                'storageCapacityReserved': False, 'effectiveHostPolicyVerified': False,
                'hostNamespacePreflightObserved': True, 'hostNamespaceReserved': False,
                'supportRecipePathsBound': True,
                'supportRecipeAccountBound': True,
                'hostPackageFileMetadataMatched': True, 'hostPackageFilesObserved': 3,
                'hostPackagePublisherAuthenticityVerified': False, 'hostPackageVersionCompatibilityVerified': False,
                'freshOriginalBound': True, 'payloadVerified': True}
        except Exception as error:
            poisoned = True
            raise C.AllocationUncertain('Owned target payload observation changed; preserve both targets') from error
    observe()
    return {'check': lambda: observe(), 'observe': observe, 'layout': original}


def populate_new_targets(stack, user, source, recipes, raw):
    """Future trusted installer only, fixed /opt and /etc, no override or activation."""
    return _populate_new_targets(stack, user, source, recipes, raw, tuple(map(str, C.PARENTS)))


def main(argv=None):
    raise ValueError('No owned-target install, registration, adoption or recovery CLI exposed')


if __name__ == '__main__':
    print('No owned-target CLI; no installation or recovery performed', file=sys.stderr)
    sys.exit(1)
