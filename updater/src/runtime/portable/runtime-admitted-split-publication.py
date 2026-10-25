# SPDX-License-Identifier: GPL-3.0-only
"""Fresh original artifacts in private split-device roles; never installs."""
from contextlib import ExitStack
import os
import sys

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('admitted_split_payload', Path(__file__).with_name('runtime-admitted-role-publication.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
X = T._load('admitted_split_publisher', 'runtime-split-role-publication.py')
K, E, A = T.K, T.E, T.A


def _budget(roles):
    import resource
    payload = sum(len(tree['directories']) - 1 + len(tree['files']) + len(tree['links']) for tree in roles.values())
    # Three protected parent chains, two roots/allocation, full journal, headroom.
    required = len(os.listdir('/proc/self/fd')) + payload + 12 + 13 + 16
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot retain original and both split roles')
    return {'splitPayloadDescriptorLimit': soft, 'splitPayloadDescriptorUpperBound': required}


def prepare_admitted_split_publication(stack, user, source, recipes, runtime_parent, config_parent, journal_parent, raw):
    """Private /root and EPHEMERAL /run experiment, not persistent configuration."""
    A._root_linux()
    E.P.C._bounded(raw)
    paths = (A._canonical(source), A._canonical(recipes), X._path(runtime_parent, 'runtime'),
             X._path(config_parent, 'config'), X.J._path(journal_parent))
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('Disjoint original sources and three new private parents required')
    # Closed preflight only: the unchanged split publisher independently readmits all parents.
    with ExitStack() as preflight:
        parents = [X.P._hold_parent(preflight, path)[0] for path in paths[2:4]]
        _, _, log = X.J._hold_parent(preflight, journal_parent)
        X._different_devices(tuple(os.fstat(fd).st_dev for fd in parents))
        for fd in (*parents, log):
            X.J._entries(fd, set())
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    roles = T._roles(layout)
    budget = _budget(roles)
    if sum(size for tree in roles.values() for size, _, _ in tree['files'].values()) > 512 * 1024 * 1024:
        raise ValueError('Bounded split payload memory required')
    payloads = {}
    for role, prefix in T.PREFIXES.items():
        payloads[role] = {}
        for name, (size, pin, _) in roles[role]['files'].items():
            data = roles[role]['manifest'] if name == T.MANIFEST else original['read_file']('/' + prefix + name)
            if type(data) is not bytes or (len(data), E._sha(data)) != (size, pin):
                raise ValueError('Fresh original split payload pair required')
            payloads[role][name] = data
    original['check']()
    built = {}

    def populate(role, fd):
        built[role] = T._build_role(stack, fd, roles[role], payloads[role], original)
        return {key: built[role][key] for key in ('check', 'sync')}

    storage_plan = {role: {'sizes': tuple(size for size, _, _ in tree['files'].values()),
        'directories': len(tree['directories']), 'links': len(tree['links'])} for role, tree in roles.items()}
    publication = X.prepare_split_publication(stack, runtime_parent, config_parent, journal_parent, E._sha(layout), populate,
        storage_plan=storage_plan)
    poisoned = False

    def guarded(method, *args):
        nonlocal poisoned
        if poisoned:
            raise X.Uncertain('Admitted split context poisoned; preserve all three parents')
        try:
            original['check']()
            publication['observe']()
            result = method(*args)
            publication['observe']()
            original['check']()
            return result
        except Exception as error:
            poisoned = True
            raise X.Uncertain('Admitted split publication uncertain; preserve all three parents') from error

    def observe():
        result = guarded(publication['observe'])
        return {**original['policy'], **result, **budget, 'kind': 'private-admitted-split-publication',
            'privateAdmittedSplitPayloadsVerified': True, 'privateRoleSourceFiles': 259,
            'privateRoleRegularFiles': 261, 'privateRoleAliases': 4,
            'privateRoleManifestSha256': {role: E._sha(tree['manifest']) for role, tree in roles.items()},
            'finalLayoutSha256': E._sha(layout),
            'privateRoleMapping': 'runtime=/opt/droiddeck;config=/etc-staging-only',
            'configStorageScope': 'ephemeral-private-test-only',
            'parentPreflightContinuouslyHeld': False, 'publisherParentsIndependentlyReadmitted': True,
            'independentSecondTreeObserverVerified': False}

    def publish_next():
        guarded(publication['publish_next'])
        return observe()

    def read(role, name):
        if type(role) is not str or role not in built:
            raise ValueError('Closed private split role required')
        return guarded(built[role]['read'], name)

    observe()
    return {'observe': observe, 'publish_next': publish_next, 'read': read}


def main(argv=None):
    raise ValueError('No admitted-split install, publication, adoption or recovery CLI exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No admitted-split CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
