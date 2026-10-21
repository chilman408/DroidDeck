# SPDX-License-Identifier: GPL-3.0-only
"""Fixed PAM/tmpfiles basename and runtime-state absence preflight, read only."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

spec = importlib.util.spec_from_file_location('support_unit_namespace', Path(__file__).with_name('runtime-unit-namespace.py'))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
A, S, R = U.A, U.S, U.R
PAM_DIRS = tuple(map(PurePosixPath, ('/etc/pam.d', '/usr/lib/pam.d')))
TMP_DIRS = tuple(map(PurePosixPath, ('/etc/tmpfiles.d', '/run/tmpfiles.d', '/usr/local/lib/tmpfiles.d', '/usr/lib/tmpfiles.d')))
STATE_ROOTS = tuple(map(PurePosixPath, ('/var/lib/droiddeck', '/run/droiddeck')))
PAM = b'#%PAM-1.0\naccount required pam_unix.so\nsession required pam_systemd.so class=background\n'
MAX_ENTRIES = 4096


def _budget():
    import resource
    paths = (*PAM_DIRS, *TMP_DIRS, *(p.parent for p in STATE_ROOTS))
    nodes = {p for path in paths for p in (path, *path.parents)}
    required = len(os.listdir('/proc/self/fd')) + len(nodes) + 4
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and required > soft:
        raise ValueError('Existing descriptor limit cannot hold support namespace')
    return {'supportDescriptorLimit': soft, 'supportDescriptorUpperBound': required}


def hold_support_namespace(stack, *, prepared_state=False):
    A._root_linux()
    budget = _budget()
    nodes, missing = {}, {}

    def directory(path):
        if path in nodes:
            return nodes[path][0]
        if path in missing:
            return None
        parent = None if path == PurePosixPath('/') else directory(path.parent)
        if path != PurePosixPath('/') and parent is None:
            missing[path] = None
            return None
        name = '/' if parent is None else path.name
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            identity = S._directory(before, False)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            missing[path] = parent, name
            return None
        stack.callback(os.close, fd)
        if S._directory(os.fstat(fd), False) != identity:
            raise ValueError('Original protected support directory generation required')
        nodes[path] = fd, parent, name, identity
        return fd

    roots = [(path, directory(path), 'droiddeck') for path in PAM_DIRS]
    if roots[0][1] is None:
        raise ValueError('Protected /etc/pam.d required; legacy PAM fallback not admitted')
    roots += [(path, directory(path), 'droiddeck.conf') for path in TMP_DIRS]
    states = [(path, directory(path.parent)) for path in STATE_ROOTS]
    prepared = None
    if prepared_state:
        path = STATE_ROOTS[0]
        fd = directory(path)
        if fd is None:
            raise ValueError('Original image/profile preparation state required')
        children = {}
        for name in ('image-staging', 'waydroid-profile'):
            child = directory(path / name)
            if child is None:
                raise ValueError('Both protected preparation directories required')
            children[name] = child
        prepared = fd, children

    def prepared_check():
        if prepared is None:
            return
        fd, children = prepared
        if set(os.listdir(fd)) != set(children):
            raise ValueError('Only image/profile preparation state admitted')
        for current in (fd, *children.values()):
            info = os.fstat(current)
            if ((info.st_uid, info.st_gid) != (0, 0) or stat.S_IMODE(info.st_mode) != 0o700):
                raise ValueError('Private root-owned preparation directories required')

    def scan(fd, count):
        before = A._identity(os.fstat(fd))
        rows = {}
        with os.scandir(fd) as entries:
            for entry in entries:
                count[0] += 1
                if count[0] > MAX_ENTRIES:
                    raise ValueError('Bounded complete support namespace inventory required')
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                rows[entry.name] = A._identity(info)
        if A._identity(os.fstat(fd)) != before:
            raise ValueError('Stable support directory inventory required')
        return rows

    count = [0]
    inventory = {}
    for path, fd, leaf in roots:
        if fd is not None:
            R._absent(fd, leaf)
            inventory[path] = scan(fd, count)
            if leaf in inventory[path]:
                raise ValueError('Support name appeared during admission')
    prepared_check()
    for path, fd in states[1:] if prepared_state else states:
        if fd is not None:
            R._absent(fd, path.name)

    def check():
        A._root_linux()
        for fd, parent, name, identity in nodes.values():
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if S._directory(info, False) != identity:
                    raise ValueError('Original support ancestor required')
        for absent in missing.values():
            if absent is not None:
                R._absent(*absent)
        fresh = [0]
        for path, fd, leaf in roots:
            if fd is not None:
                R._absent(fd, leaf)
                if scan(fd, fresh) != inventory[path]:
                    raise ValueError('Original complete support inventory required')
        prepared_check()
        for path, fd in states[1:] if prepared_state else states:
            if fd is not None:
                R._absent(fd, path.name)

    check()
    return {'check': check, 'policy': {**budget,
        'kind': 'held-fixed-support-namespace', 'supportNamePreflightVerified': True,
        'pamServiceNamesAbsentObserved': 2, 'tmpfilesBasenamesAbsentObserved': 4,
        'runtimeStateRootsAbsentObserved': 1 if prepared_state else 2,
        'preparationStateNamespaceVerified': prepared_state, 'supportEntriesObserved': count[0],
        **dict.fromkeys(('supportRecipePathsBound', 'effectivePamPolicyVerified', 'pamVendorBuildPathsVerified',
            'pamModuleCodeVerified', 'effectiveTmpfilesPolicyVerified', 'tmpfilesRuleConflictsVerified',
            'runtimeStateCreated', 'targetReserved', 'targetOwnershipClaimed', 'filesWritten',
            'servicesRegistered', 'servicesActivated', 'registrationNamespaceVerified',
            'installationAuthorized', 'recoveryAuthorized', 'installable', 'readyForActivation'), False)}}


def _validate_support_recipes(pam, tmpfiles, *, expected_gid=None):
    if type(pam) is not bytes or type(tmpfiles) is not bytes:
        raise ValueError('Exact original support recipe bytes required')
    match = re.fullmatch(rb'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root ([1-9][0-9]{0,9}) -\n', tmpfiles)
    if pam != PAM or match is None or not 0 < int(match[1]) < 2**31:
        raise ValueError('Exact original support recipe and fixed state paths required')
    if expected_gid is not None and (type(expected_gid) is not int or not 1000 <= expected_gid < 2**31
                                    or int(match[1]) != expected_gid):
        raise ValueError('Support recipe must match the original runtime account group')


def hold_final_support_namespace(stack, user, source, recipes, raw):
    A._root_linux()
    R.K.E.P.C._bounded(raw)
    namespace = hold_support_namespace(stack)
    held = U.hold_final_unit_namespace(stack, user, source, recipes, raw)

    def check():
        namespace['check']()
        held['check']()
        namespace['check']()

    def read(method, *args):
        check()
        data = held[method](*args)
        check()
        return data

    pam = read('read_file', '/etc/pam.d/droiddeck')
    tmpfiles = read('read_file', '/etc/tmpfiles.d/droiddeck.conf')
    _validate_support_recipes(pam, tmpfiles)
    check()
    return {'check': check, 'read_plan': lambda: read('read_plan'), 'read_file': lambda name: read('read_file', name),
        'policy': {**held['policy'], **namespace['policy'], 'supportRecipePathsBound': True,
            'kind': 'held-final-fixed-support-namespace'}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as stack:
        if argv == ['--inspect-support-namespace']:
            held = hold_support_namespace(stack)
        elif len(argv) == 5 and argv[0] == '--inspect-final-support-namespace' and argv[4] == '-':
            held = hold_final_support_namespace(stack, argv[1], argv[2], argv[3], sys.stdin.buffer.read(R.K.E.P.C.LIMIT + 1))
        else:
            raise ValueError('Only fixed read-only support namespace inspection exposed')
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, U.subprocess.SubprocessError, R.K.E.P.configparser.Error):
        print('Support namespace preflight refused; no files or services changed', file=sys.stderr)
        sys.exit(1)
