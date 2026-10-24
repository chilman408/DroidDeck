# SPDX-License-Identifier: GPL-3.0-only
"""Repeatable offline registration phase; original journals remain authoritative."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('installer_session_registration', Path(__file__).with_name('runtime-session-recovery.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
R = S.R
MODES = ('--install-registration', '--resume-registration')


def mapped(prefix, name):
    return str(Path(prefix) / name.lstrip('/'))


def lock(stack, prefix='/'):
    import fcntl
    dirs = R._Directories(stack)
    parent = dirs.open(mapped(prefix, '/var/lib/droiddeck'))
    # Cooperative lock only: other root administrators/old component CLIs are not
    # reserved against. Existing component ownership and inactive guards still run.
    fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
    dirs.check()
    return dirs.check


def present(stack, path):
    dirs = R._Directories(stack)
    path = Path(path)
    parent = dirs.open(path.parent)
    try:
        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def finish_phases(authority, *, prefix='/', guard=R._inactive, after_phase=None):
    guard()
    authority['check']()
    original = R._finish(authority, mapped(prefix, R.JOURNAL), prefix, check_runtime=guard)
    if after_phase:
        after_phase('original')
    phases = []
    for name, location, files in (('session-recovery', S.JOURNAL, S.FILES),
                                  ('headless-reset', S.RESET_JOURNAL, S.RESET_FILES)):
        journal = mapped(prefix, location)
        guard()
        authority['check']()
        with ExitStack() as stack:
            exists = present(stack, journal)
        if not exists:
            S.prepare(authority, journal, prefix, guard=guard, files=files)
        # Existing empty/partial/foreign journals are NEVER treated as fresh.
        result = S.finish(authority, journal, prefix, guard=guard, files=files)
        phases.append({'phase': name, 'registeredFiles': result['registeredFiles']})
        if after_phase:
            after_phase(name)
    guard()
    authority['check']()
    return {'kind': 'installer-registration-phase', 'originalRegisteredFiles': original['registeredFiles'],
            'recoveryPhases': phases, 'registeredFiles': 12, 'registrationRecoveryVerified': True,
            'servicesActivated': False, 'servicesEnabled': False, 'daemonReloaded': False,
            'androidOperationsExecuted': False, 'preIntentRecoverySupported': False,
            'wholeInstallationVerified': False, 'installable': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 5 or args[0] not in MODES or args[-1] != '-':
        raise ValueError('Explicit install-registration or resume-registration USER ASSEMBLY RECIPES - required')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    R.O.E.P.C._bounded(raw)
    R._inactive()
    with ExitStack() as stack:
        check_lock = lock(stack)
        if args[0] == '--install-registration':
            dirs = R._Directories(stack)
            R._absent(dirs.open(Path(R.JOURNAL).parent), Path(R.JOURNAL).name)
            owned = R.O.populate_new_targets(stack, args[1], args[2], args[3], raw)
            authority = owned['layout']
            check_lock()
            R._prepare(authority, owned, R.JOURNAL, check_runtime=R._inactive)
        else:
            authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)

        def guard():
            check_lock()
            R._inactive()
        result = finish_phases(authority, guard=guard)
        check_lock()
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Registration phase incomplete; preserve all original targets and journals; no services activated', file=sys.stderr)
        sys.exit(1)
