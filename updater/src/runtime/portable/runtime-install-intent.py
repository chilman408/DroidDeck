# SPDX-License-Identifier: GPL-3.0-only
"""Exclusive durable intent for a new install, never installation or recovery."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import sys

spec = importlib.util.spec_from_file_location('intent_retained_candidate', Path(__file__).with_name('runtime-owned-install-admission.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
A, E, S = G.A, G.E, G.S
TARGETS = (PurePosixPath('/opt/droiddeck'), PurePosixPath('/etc/droiddeck'))
JOURNAL = PurePosixPath('/var/lib/droiddeck-install-intent')


class InstallIntentUncertain(RuntimeError):
    """A new private intent may exist. Preserve it; never auto-clean or retry."""


def _hold_parent(stack, path):
    chain, current = [], None
    for part in path.parts:
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        identity = S._directory(os.fstat(fd), False)
        chain.append((current, part, fd, identity))
        current = fd

    def check():
        A._root_linux()
        for ancestor, name, fd, identity in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if S._directory(info, False) != identity:
                    raise ValueError('Original protected intent or installation parent required')

    check()
    return {'check': check, 'fd': current}


def _absent(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('Existing installation target or intent must never be adopted')


def prepare_install_intent(stack, user, source, recipes, candidate_parent, raw):
    return _prepare_install_intent(stack, user, source, recipes, candidate_parent, raw, G.hold_owned_install_candidate)


def _prepare_install_intent(stack, user, source, recipes, candidate_parent, raw, admit):
    A._root_linux()
    E.P.C._bounded(raw)
    inputs = tuple(map(A._canonical, (source, recipes, candidate_parent)))
    for original in inputs:
        for output in (*TARGETS, JOURNAL):
            if original.is_relative_to(output) or output.is_relative_to(original):
                raise ValueError('Disjoint original inputs and fixed intent/install namespaces required')
    targets = [(path, _hold_parent(stack, path.parent)) for path in TARGETS]
    parent = _hold_parent(stack, JOURNAL.parent)

    def namespaces():
        for path, held in targets:
            held['check']()
            _absent(held['fd'], path.name)
        parent['check']()

    def before_write():
        namespaces()
        _absent(parent['fd'], JOURNAL.name)

    before_write()
    candidate = admit(stack, user, *inputs, raw)
    candidate['check']()
    plan = candidate['read_plan']()
    before_write()
    # Namespace observations are not reservations or ownership of either target.
    record = E._encode({'version': 1, 'product': 'DroidDeck', 'kind': 'new-install-intent',
        'transaction': secrets.token_hex(16), 'phase': 'prepared',
        'planSha256': E._sha(plan), 'candidateManifestSha256': candidate['policy']['candidateManifestSha256'],
        'targets': [{'path': str(path), 'parentDevice': os.fstat(held['fd']).st_dev,
                     'parentInode': os.fstat(held['fd']).st_ino} for path, held in targets],
        'targetReserved': False, 'installationAuthorized': False, 'recoveryAuthorized': False,
        'targetOwnershipClaimed': False, 'servicesActivated': False}, compact=True)
    candidate['check']()
    before_write()
    created = False
    try:
        os.mkdir(JOURNAL.name, 0o700, dir_fd=parent['fd'])
        created = True
        initial = os.stat(JOURNAL.name, dir_fd=parent['fd'], follow_symlinks=False)
        directory = os.open(JOURNAL.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent['fd'])
        stack.callback(os.close, directory)
        identity = S._directory(os.fstat(directory), True)
        if identity != S._directory(initial, True):
            raise ValueError('Original new intent directory required')
        S._entries(directory, set())
        fd, info, _ = S._write_private(stack, directory, 'intent.json', record)

        def check():
            candidate['check']()
            namespaces()
            for actual in (os.fstat(directory), os.stat(JOURNAL.name, dir_fd=parent['fd'], follow_symlinks=False)):
                if S._directory(actual, True) != identity:
                    raise ValueError('Original created intent namespace required')
            S._entries(directory, {'intent.json'})
            for after_read in (False, True):
                if after_read and A._read(fd, len(record)) != record:
                    raise ValueError('Original intent bytes changed')
                for actual in (os.fstat(fd), os.stat('intent.json', dir_fd=directory, follow_symlinks=False)):
                    if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                            or (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) != (0, 0, 0o600)
                            or A._identity(actual) != A._identity(info)):
                        raise ValueError('Original private single-link intent file required')
            candidate['check']()
            namespaces()

        check()
        os.fsync(directory)
        os.fsync(parent['fd'])
        check()
    except Exception as error:
        if created:
            raise InstallIntentUncertain('Private intent may exist; preserve it and inspect before retry') from error
        raise

    def retained_check():
        try:
            check()
        except Exception as error:
            raise InstallIntentUncertain('Created intent changed; preserve it and inspect before retry') from error

    def read_intent():
        retained_check()
        result = bytes(record)
        retained_check()
        return result

    def read_original(name):
        retained_check()
        result = candidate['read'](name)
        retained_check()
        return result

    def read_plan():
        retained_check()
        result = candidate['read_plan']()
        retained_check()
        return result

    return {'check': retained_check, 'read_intent': read_intent, 'read_candidate': read_original,
        'read_plan': read_plan, 'policy': {
        **candidate['policy'], 'kind': 'prepared-new-install-intent', 'intentPath': str(JOURNAL),
        'intentSha256': E._sha(record), 'newPrivateIntentCreated': True, 'intentDurabilityVerified': True,
        'newInstallTargetsAbsentObserved': True, 'targetReserved': False, 'targetOwnershipClaimed': False,
        'installationAuthorized': False, 'recoveryAuthorized': False}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--prepare-new-install-intent' or argv[5] != '-':
        raise ValueError('Only exclusive new-install intent preparation is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        result = prepare_install_intent(stack, argv[1], argv[2], argv[3], argv[4], raw)
        result['check']()
        print(json.dumps(result['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except InstallIntentUncertain:
        print('Installation intent uncertain; preserve its directory and inspect before retry', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Installation intent refused; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
