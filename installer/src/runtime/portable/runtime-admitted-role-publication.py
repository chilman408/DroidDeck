# SPDX-License-Identifier: GPL-3.0-only
"""Fresh original-layout payloads in two privately published roles; never installs."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load('role_prepared_stage', 'runtime-prepared-stage.py')
P = _load('role_owned_publication', 'runtime-owned-publication.py')
K, E, A, S = R.K, R.E, R.A, R.S
MANIFEST = 'role-layout.json'
PREFIXES = {'runtime': 'opt/droiddeck/', 'config': 'etc/'}


def _roles(raw):
    tree = R.Q._spec(raw)
    roles = {}
    for role, prefix in PREFIXES.items():
        files = {name[len(prefix):]: pair for name, pair in tree['files'].items() if name.startswith(prefix)}
        links = {name[len(prefix):]: target for name, target in tree['links'].items() if name.startswith(prefix)}
        directories = {'': 0o700}
        for name in (*files, *links):
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.':
                    directories[str(parent)] = tree['directories'][prefix + str(parent)]
        if MANIFEST in files or MANIFEST in links or MANIFEST in directories:
            raise ValueError('Private role metadata must not collide with original files')
        manifest = E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'private-admitted-role',
            'role': role, 'originalPrefix': '/' + prefix, 'finalLayoutSha256': E._sha(raw),
            'files': [{'path': name, 'size': size, 'sha256': pin, 'mode': mode, 'uid': 0, 'gid': 0}
                      for name, (size, pin, mode) in sorted(files.items())],
            'directories': [{'path': name, 'mode': mode, 'uid': 0, 'gid': 0} for name, mode in sorted(directories.items())],
            'links': [{'path': name, 'target': target, 'uid': 0, 'gid': 0} for name, target in sorted(links.items())],
            'installable': False, 'readyForActivation': False}, compact=True)
        files[MANIFEST] = len(manifest), E._sha(manifest), 0o600
        roles[role] = {'files': files, 'links': links, 'directories': directories, 'manifest': manifest}
    regular = {prefix + name for role, prefix in PREFIXES.items() for name in roles[role]['files'] if name != MANIFEST}
    aliases = {prefix + name for role, prefix in PREFIXES.items() for name in roles[role]['links']}
    if (regular != set(tree['files']) - {'final-layout.json', A.MANIFEST}
            or aliases != set(tree['links']) or len(regular) != 259 or len(aliases) != 4):
        raise ValueError('All 259 original payload destinations and four aliases must map exactly once')
    return roles


def _budget(roles):
    import resource
    payload = sum(len(t['directories']) - 1 + len(t['files']) + len(t['links']) for t in roles.values())
    # Outer private publisher/parents/allocations + separate journal lifetime + headroom.
    required = len(os.listdir('/proc/self/fd')) + payload + 9 + 13 + 16
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor budget cannot hold original and both admitted roles')
    return {'rolePayloadDescriptorLimit': soft, 'rolePayloadDescriptorUpperBound': required}


def _build_role(stack, root, tree, payload, original, *, metadata_name=MANIFEST):
    if metadata_name not in (None, MANIFEST):
        raise ValueError('Fixed private metadata name or no metadata required')
    root_identity = R.Q._directory(os.fstat(root), 0o700)
    directories, files, links = {'': {'fd': root}}, {}, {}
    inventory = {name: set() for name in tree['directories']}
    for name in (*tree['files'], *tree['links'], *(name for name in tree['directories'] if name)):
        group, _, leaf = name.rpartition('/')
        inventory[group].add(leaf)
    original['check']()
    S._entries(root, set())
    space = os.fstatvfs(root)
    if space.f_bavail * space.f_frsize < sum(map(len, payload.values())) + 1024 * 1024:
        raise ValueError('Insufficient private role payload space')
    for name in sorted((name for name in tree['directories'] if name), key=lambda n: (n.count('/'), n)):
        group, _, leaf = name.rpartition('/')
        parent = directories[group]['fd']
        os.mkdir(leaf, 0o700, dir_fd=parent)
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if R.Q._directory(os.fstat(fd), 0o700) != R.Q._directory(before, 0o700):
            raise ValueError('Original new role directory required')
        entry = {'parent': parent, 'name': leaf, 'fd': fd, 'info': before, 'mode': 0o700}
        R._chmod_created(entry, tree['directories'][name], directory=True)
        directories[name] = entry

    def copied(name):
        group, _, leaf = name.rpartition('/')
        parent = directories[group]['fd']
        data = payload[name]
        if (len(data), E._sha(data)) != tree['files'][name][:2]:
            raise ValueError('Original role payload pair required')
        fd, info, _ = S._write_private(stack, parent, leaf, data)
        entry = {'parent': parent, 'name': leaf, 'fd': fd, 'info': info, 'mode': 0o600}
        R._chmod_created(entry, tree['files'][name][2])
        if A._read(fd, len(data)) != data:
            raise ValueError('Exclusively created role bytes changed')
        R._file(entry)
        files[name] = entry

    for name in sorted(name for name in tree['files'] if name != metadata_name):
        copied(name)
    for name, target in sorted(tree['links'].items()):
        group, _, leaf = name.rpartition('/')
        parent = directories[group]['fd']
        os.symlink(target, leaf, dir_fd=parent)
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        fd = os.open(leaf, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, fd)
        if A._identity(os.fstat(fd)) != A._identity(before):
            raise ValueError('Original newly created role alias required')
        links[name] = parent, leaf, fd, before, target
    if metadata_name is not None:
        copied(metadata_name)

    def check_created():
        if R.Q._directory(os.fstat(root), 0o700) != root_identity:
            raise ValueError('Original private role root required')
        for name, entry in directories.items():
            if name:
                R._directory(entry)
            S._entries(entry['fd'], inventory[name])
        for name, entry in files.items():
            R._file(entry)
            if A._read(entry['fd'], len(payload[name])) != payload[name]:
                raise ValueError('Original admitted role bytes changed')
            R._file(entry)
        for parent, leaf, fd, before, target in links.values():
            for after_read in (False, True):
                if after_read and os.readlink(leaf, dir_fd=parent) != target:
                    raise ValueError('Original direct role library alias required')
                for info in (os.fstat(fd), os.stat(leaf, dir_fd=parent, follow_symlinks=False)):
                    if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1)
                            or stat.S_IMODE(info.st_mode) != 0o777 or A._identity(info) != A._identity(before)):
                        raise ValueError('Original root-owned role alias generation required')

    def check():
        original['check']()
        check_created()
        original['check']()

    def sync():
        check()
        for entry in files.values():
            os.fsync(entry['fd'])
        for name in sorted(directories, key=lambda n: (n.count('/'), n), reverse=True):
            os.fsync(directories[name]['fd'])
        check()

    def read(name):
        if type(name) is not str or name not in files:
            raise ValueError('Closed original admitted role regular read required')
        check()
        result = A._read(files[name]['fd'], len(payload[name]))
        check()
        return result

    check()
    return {'check': check, 'sync': sync, 'read': read}


def prepare_admitted_role_publication(stack, user, source, recipes, parent, journal_parent, raw):
    A._root_linux()
    E.P.C._bounded(raw)
    paths = (A._canonical(source), A._canonical(recipes), P._parent_path(parent), P.J._path(journal_parent))
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('Disjoint original sources and new private publication parents required')
    # Empty-parent preflight is closed before fresh admission; the publisher readmits both.
    with ExitStack() as preflight:
        parent_fd, _ = P._hold_parent(preflight, paths[2])
        _, _, journal_fd = P.J._hold_parent(preflight, journal_parent)
        S._entries(parent_fd, set())
        S._entries(journal_fd, set())
    original = K.hold_final_install_layout(stack, user, source, recipes, raw)
    layout = original['read_plan']()
    roles = _roles(layout)
    budget = _budget(roles)
    if sum(size for tree in roles.values() for size, _, _ in tree['files'].values()) > 512 * 1024 * 1024:
        raise ValueError('Bounded private role payload memory required')
    payloads = {}
    for role, prefix in PREFIXES.items():
        payloads[role] = {}
        for name, (size, pin, _) in roles[role]['files'].items():
            data = roles[role]['manifest'] if name == MANIFEST else original['read_file']('/' + prefix + name)
            if type(data) is not bytes or (len(data), E._sha(data)) != (size, pin):
                raise ValueError('Fresh independent original role payload pair required')
            payloads[role][name] = data
    original['check']()
    built = {}

    def populate(role, fd):
        built[role] = _build_role(stack, fd, roles[role], payloads[role], original)
        return {key: built[role][key] for key in ('check', 'sync')}

    publication = P.prepare_private_publication(stack, parent, journal_parent, E._sha(layout), populate)
    poisoned = False

    def guarded(method, *args):
        nonlocal poisoned
        if poisoned:
            raise P.PublicationUncertain('Admitted role publication poisoned; preserve both parents')
        try:
            original['check']()
            publication['observe']()
            result = method(*args)
            publication['observe']()
            original['check']()
            return result
        except Exception as error:
            poisoned = True
            raise P.PublicationUncertain('Admitted role publication uncertain; preserve both parents') from error

    def observe():
        result = guarded(publication['observe'])
        return {**original['policy'], **result, **budget, 'kind': 'private-admitted-role-publication',
            'privateRolePayloadsVerified': True, 'privateRoleSourceFiles': 259,
            'privateRoleRegularFiles': 261, 'privateRoleAliases': 4,
            'privateRoleManifestSha256': {role: E._sha(tree['manifest']) for role, tree in roles.items()},
            'finalLayoutSha256': E._sha(layout), 'privateRoleMapping': 'runtime=/opt/droiddeck;config=/etc-staging-only',
            'parentPreflightContinuouslyHeld': False, 'publisherParentsIndependentlyReadmitted': True,
            'independentSecondTreeObserverVerified': False, 'crossFilesystemPublicationVerified': False}

    def publish_next():
        guarded(publication['publish_next'])
        return observe()

    def read(role, name):
        if type(role) is not str or role not in built:
            raise ValueError('Closed admitted private role required')
        return guarded(built[role]['read'], name)

    observe()
    return {'observe': observe, 'publish_next': publish_next, 'read': read}


def main(argv=None):
    raise ValueError('No live installation, activation, adoption or recovery CLI exposed')


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('No admitted-role CLI; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
