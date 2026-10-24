# SPDX-License-Identifier: GPL-3.0-only
"""Read-only new-install namespace admission, not reservation or installation."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys

_spec = importlib.util.spec_from_file_location('new_target_placement_plan', Path(__file__).with_name('runtime-install-plan.py'))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)
A, S = L.A, L.P.S
TARGET_PARENT = PurePosixPath('/opt')
TARGET_NAME = 'droiddeck'


def _hold_new_target(stack, parent, name):
    A._root_linux()
    parent = A._canonical(parent)
    if name != TARGET_NAME:
        raise ValueError('Fixed new runtime target name required')
    chain, current = [], None
    for part in parent.parts:
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        identity = S._directory(os.fstat(fd), False)
        chain.append((current, part, fd, identity))
        current = fd
    parent_fd = current

    def check():
        A._root_linux()
        for ancestor, part, fd, before in chain:
            for info in (os.fstat(fd), os.stat(part, dir_fd=ancestor, follow_symlinks=False)):
                if S._directory(info, False) != before:
                    raise ValueError('Original protected installation parent required')
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('New installation target must be absent; existing targets are never adopted')

    check()
    return {'check': check, 'parent_fd': parent_fd}


def hold_new_runtime_target(stack, user, source):
    A._root_linux()
    source = A._canonical(source)
    if source.is_relative_to(L.TARGET) or L.TARGET.is_relative_to(source):
        raise ValueError('Disjoint verified source and fixed installation target required')
    target = _hold_new_target(stack, TARGET_PARENT, TARGET_NAME)
    plan = L.hold_runtime_install_plan(stack, user, source)

    def check():
        target['check']()
        plan['check']()
        target['check']()

    def read_plan():
        check()
        raw = plan['read_plan']()
        check()
        return raw

    def read_source(name):
        check()
        raw = plan['read_source'](name)
        check()
        return raw

    check()
    return {'check': check, 'read_plan': read_plan, 'read_source': read_source,
        'parent_fd': target['parent_fd'], 'policy': {
            **plan['policy'], 'kind': 'held-new-runtime-target-admission',
            'targetNamespaceVerified': True, 'targetAbsentObserved': True,
            'targetReserved': False, 'installationAuthorized': False}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != '--inspect-new-target':
        raise ValueError('Only fixed read-only new-target admission is exposed')
    with ExitStack() as stack:
        held = hold_new_runtime_target(stack, argv[1], argv[2])
        held['read_plan']()
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('New runtime target admission refused; no changes performed', file=sys.stderr)
        sys.exit(1)
