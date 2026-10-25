# SPDX-License-Identifier: GPL-3.0-only
"""Read-only fixed /etc registration-path guards; never reserve or install."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

spec = importlib.util.spec_from_file_location('registration_final_layout', Path(__file__).with_name('runtime-final-layout.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
A, S = K.A, K.F.S
TARGETS = tuple(PurePosixPath(path) for path in sorted(K.REGISTRATIONS.values()))
SYSTEMD = PurePosixPath('/etc/systemd/system')
UNITS = ('droiddeck-browser-connection.service', 'tesla-waydroid-compositor.service',
         'tesla-waydroid-headless-session.service', 'tesla-waydroid-runtime.service',
         'waydroid-container.service')
DROPINS = tuple(SYSTEMD / (name + '.d') for name in UNITS)


def _absent(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('Existing registration paths must never be adopted')


def hold_registration_paths(stack):
    """Hold protected ancestors and absent names, not effective unit precedence."""
    A._root_linux()
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
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            missing[path] = (parent, name)
            return None
        stack.callback(os.close, fd)
        identity = S._directory(os.fstat(fd), False)
        nodes[path] = (fd, parent, name, identity)
        return fd

    parents = [(path, directory(path.parent)) for path in TARGETS]
    dropins = [(path, directory(path)) for path in DROPINS]

    def check():
        A._root_linux()
        for fd, parent, name, identity in nodes.values():
            for info in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
                if S._directory(info, False) != identity:
                    raise ValueError('Original protected registration ancestor required')
        # A missing ancestor must remain absent; newly created parents need fresh admission.
        for absent in missing.values():
            if absent is not None:
                _absent(*absent)
        for path, fd in parents:
            if fd is not None:
                _absent(fd, path.name)
        for _, fd in dropins:
            if fd is not None:
                S._entries(fd, set())

    check()
    return {'check': check, 'policy': {
        'kind': 'held-fixed-etc-registration-paths', 'registrationPathPreflightVerified': True,
        'registrationPathsAbsentObserved': len(TARGETS), 'affectedDropInDirectoriesChecked': len(DROPINS),
        'existingRegistrationParentsMustNotBeChmodded': True,
        **dict.fromkeys(('registrationNamespaceVerified', 'effectiveSystemdNamespaceVerified',
            'targetNamespaceVerified', 'targetReserved', 'targetOwnershipClaimed', 'filesWritten',
            'servicesRegistered', 'servicesActivated', 'installationAuthorized', 'recoveryAuthorized',
            'installable', 'readyForActivation'), False)}}


def hold_final_registration_paths(stack, user, source, recipes, raw):
    A._root_linux()
    K.E.P.C._bounded(raw)
    namespace = hold_registration_paths(stack)
    held = K.hold_final_install_layout(stack, user, source, recipes, raw)
    original = held['read_plan']()
    value = json.loads(original)
    if (value['registrationTargets'] != list(map(str, TARGETS))
            or sorted(row['path'] for row in value['files'] if row['registration']) != list(map(str, TARGETS))):
        raise ValueError('Fixed registration paths must match original placement authority')

    def check():
        namespace['check']()
        held['check']()
        if held['read_plan']() != original:
            raise ValueError('Original placement generation changed')
        namespace['check']()

    def read_plan():
        check()
        result = held['read_plan']()
        check()
        return result

    def read_file(destination):
        check()
        result = held['read_file'](destination)
        check()
        return result

    check()
    return {'check': check, 'read_plan': read_plan, 'read_file': read_file,
        'policy': {**held['policy'], **namespace['policy'], 'kind': 'held-final-registration-path-preflight'}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as stack:
        if argv == ['--inspect-registration-paths']:
            held = hold_registration_paths(stack)
        elif len(argv) == 5 and argv[0] == '--inspect-final-registration-paths' and argv[4] == '-':
            raw = sys.stdin.buffer.read(K.E.P.C.LIMIT + 1)
            held = hold_final_registration_paths(stack, argv[1], argv[2], argv[3], raw)
        else:
            raise ValueError('Only fixed read-only registration path preflight is exposed')
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError,
            subprocess.SubprocessError, K.E.P.configparser.Error):
        print('Registration path preflight refused; no files or services changed', file=sys.stderr)
        sys.exit(1)
