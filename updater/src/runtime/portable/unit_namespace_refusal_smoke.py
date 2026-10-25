# SPDX-License-Identifier: GPL-3.0-only
"""New read-only real-manager refusal proof on populated .215, no installation."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('populated_unit_namespace', Path(__file__).with_name('runtime-unit-namespace.py'))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)


def snapshot():
    paths = [Path('/etc/systemd/system') / name for name in U.OWNED]
    rows = {}
    for path in paths:
        try:
            rows[str(path)] = U.A._identity(os.stat(path, follow_symlinks=False))
        except FileNotFoundError:
            rows[str(path)] = None
    return rows, tuple(sorted(os.listdir('/etc/systemd/system')))


def main():
    U.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed populated manager refusal proof is exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    before = snapshot()
    if not any(value is not None for value in before[0].values()):
        raise ValueError('Populated original service files required')
    with mock.patch.object(U.R, 'hold_final_registration_paths', side_effect=AssertionError('No original admission')) as gate:
        try:
            with ExitStack() as stack:
                U.hold_final_unit_namespace(stack, 'hilman',
                    '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                    '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', b'{}')
        except ValueError as error:
            if str(error) != 'Existing owned system manager unit must never be adopted':
                raise ValueError('Exact existing-manager-unit refusal required') from None
        else:
            raise ValueError('Populated system manager must refuse')
        gate.assert_not_called()
    if before != snapshot():
        raise ValueError('Actual service identities or parent inventory changed')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'populated-systemd-namespace-refusal-proof', 'existingManagerUnitRefused': True,
        'originalSourceAdmissionExecuted': False, 'servicesRegistered': False, 'filesWritten': False,
        'serviceIdentitiesUnchanged': True, 'parentInventoryUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print('Systemd namespace refusal proof incomplete; no installation performed', file=sys.stderr)
        sys.exit(1)
