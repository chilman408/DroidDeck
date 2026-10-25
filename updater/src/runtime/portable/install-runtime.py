# SPDX-License-Identifier: GPL-3.0-only
"""Compose original registration, frontend and compatibility without activation."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('runtime-compat-install')
B, R = C.B, C.R
I = load('install-registration')
S = I.S
MODES = ('--install-runtime', '--resume-runtime', '--verify-runtime')


def exists(prefix, name):
    with ExitStack() as stack:
        return I.present(stack, I.mapped(prefix, name))


def verify_recovery(authority, prefix, guard):
    for journal, files in ((S.JOURNAL, S.FILES), (S.RESET_JOURNAL, S.RESET_FILES)):
        S.finish(authority, I.mapped(prefix, journal), prefix, guard=guard,
                 files=files, verify_only=True)


def finish(authority, held, source, *, mode, prefix='/', guard=None, after_phase=None):
    if mode not in MODES:
        raise ValueError('Exact offline runtime operation required')
    readonly = mode == '--verify-runtime'

    def check():
        if guard:
            guard()
        if not readonly:
            R._inactive()
        authority['check']()
        held['check']()

    check()
    browser, compat = exists(prefix, B.JOURNAL), exists(prefix, C.JOURNAL)
    if mode == '--install-runtime' and (browser or compat):
        raise ValueError('Fresh runtime install cannot adopt existing extensions')
    if compat and not browser:
        raise ValueError('Compatibility without original browser intent refused')
    if readonly:
        if not browser or not compat:
            raise ValueError('Complete runtime extension intents required for verification')
        C.install(authority, held, source, mode='--verify', prefix=prefix)
    elif compat:
        # Original/base and browser-only closed inventories intentionally contain
        # the displaced location adapter. Never replay them after compatibility.
        C.install(authority, held, source, mode='--resume', prefix=prefix)
    else:
        if not browser:
            I.finish_phases(authority, prefix=prefix, guard=check)
            if after_phase:
                after_phase('registration')
            B.install(authority, held, mode='--install-new', prefix=prefix)
        else:
            B.install(authority, held, mode='--resume', prefix=prefix)
        if after_phase:
            after_phase('browser')
        check()
        # Admit all recovery files before advancing the next ownership extension.
        verify_recovery(authority, prefix, check)
        C.install(authority, held, source, mode='--install-new', prefix=prefix)
    if after_phase and not readonly:
        after_phase('compatibility')
    check()
    verify_recovery(authority, prefix, check)
    check()
    return {'kind': 'offline-runtime-installation', 'runtimeOwnershipVerified': True,
            'registeredFilesVerified': 12, 'frontendFilesVerified': len(held['files']),
            'compatibilityInstalled': True, 'displacedOriginalRetained': True,
            'readOnly': readonly, 'servicesActivated': False, 'servicesEnabled': False,
            'daemonReloaded': False, 'androidOperationsExecuted': False,
            'tlsConfigured': False, 'wholeInstallationVerified': False, 'installable': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 7 or args[0] not in MODES or args[-1] != '-':
        raise ValueError('Runtime operation USER ASSEMBLY RECIPES FRONTEND COMPAT_SOURCE - required')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    R.O.E.P.C._bounded(raw)
    readonly = args[0] == '--verify-runtime'
    if not readonly:
        R._inactive()
    with ExitStack() as stack:
        check_lock = I.lock(stack)
        account = R.A._account(args[1])
        held = R.A._hold_pinned_tree(stack, args[4], owner=account[1:3],
                                    records_reader=B.records, manifest_limit=65536)
        dirs = R._Directories(stack)
        info = Path(args[5]).lstat()
        if not 0 < info.st_size <= 32768:
            raise ValueError('Bounded protected compatibility source required')
        original_source, _ = R._file(dirs, args[5], info.st_size, C.PIN, 0o600)

        def guard():
            check_lock()
            checked, _ = R._file(dirs, args[5], info.st_size, C.PIN, 0o600)
            if R._identity(checked) != R._identity(original_source):
                raise ValueError('Original compatibility source generation required')

        if args[0] == '--install-runtime':
            for journal in (R.JOURNAL, B.JOURNAL, C.JOURNAL):
                R._absent(dirs.open(Path(journal).parent), Path(journal).name)
            guard()
            owned = R.O.populate_new_targets(stack, args[1], args[2], args[3], raw)
            authority = owned['layout']
            R._prepare(authority, owned, R.JOURNAL, check_runtime=R._inactive)
        else:
            authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)
        print(json.dumps(finish(authority, held, args[5], mode=args[0], guard=guard), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Offline runtime incomplete; preserve original targets and extension journals; no services activated', file=sys.stderr)
        sys.exit(1)
