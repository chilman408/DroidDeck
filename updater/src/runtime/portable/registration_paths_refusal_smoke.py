# SPDX-License-Identifier: GPL-3.0-only
"""New read-only registration-path refusal on populated .215, no source execution."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('populated_registration_paths', Path(__file__).with_name('runtime-registration-paths.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def snapshot():
    targets = {}
    for path in R.TARGETS:
        try:
            targets[str(path)] = R.A._identity(os.stat(path, follow_symlinks=False))
        except FileNotFoundError:
            targets[str(path)] = None
    parents = {}
    for path in sorted({p.parent for p in R.TARGETS} | set(R.DROPINS)):
        try:
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            parents[str(path)] = None
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Original regular registration parent directories required')
        parents[str(path)] = (R.A._identity(info), tuple(sorted(os.listdir(path))))
    return targets, parents


def main():
    R.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed populated registration-path refusal proof is exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    before = snapshot()
    if not any(value is not None for value in before[0].values()):
        raise ValueError('Populated fixed registration target required')
    with mock.patch.object(R.K, 'hold_final_install_layout', side_effect=AssertionError('No original source admission')) as gate:
        try:
            with ExitStack() as stack:
                R.hold_final_registration_paths(stack, 'hilman',
                    '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                    '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', b'{}')
        except ValueError as error:
            if str(error) != 'Existing registration paths must never be adopted':
                raise ValueError('Exact existing-registration refusal required') from None
        else:
            raise ValueError('Populated registration paths must refuse')
        gate.assert_not_called()
    if snapshot() != before:
        raise ValueError('Registration target identities or parent inventories changed')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Registration proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'populated-registration-path-refusal-proof', 'existingRegistrationRefused': True,
        'originalSourceAdmissionExecuted': False, 'filesWritten': False, 'servicesRegistered': False,
        'registrationIdentitiesUnchanged': True, 'parentInventoriesUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Registration path refusal proof incomplete; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
