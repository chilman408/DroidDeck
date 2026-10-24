# SPDX-License-Identifier: GPL-3.0-only
"""Read-only new staging entry-point refusal proof on populated .215."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('populated_staging_refusal', Path(__file__).with_name('runtime-install-staging.py'))
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)


def main():
    I.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed populated-VM staging refusal proof is exposed')
    initial = set(os.listdir('/proc/self/fd'))
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    targets = {path: I.A._identity(os.stat(path, follow_symlinks=False)) for path in I.H.TARGETS}
    parents = {path.parent: set(os.listdir(path.parent)) for path in I.H.TARGETS}
    if any(not stat.S_ISDIR(os.stat(path, follow_symlinks=False).st_mode) for path in I.H.TARGETS):
        raise ValueError('Original populated installation targets required')
    if os.path.lexists(I.H.JOURNAL):
        raise ValueError('Previously absent intent namespace required')
    with mock.patch.object(I.H.G, 'hold_owned_install_candidate', side_effect=AssertionError('No candidate admission')) as gate, \
            mock.patch.object(I, '_new_directory', side_effect=AssertionError('No allocation')) as allocation:
        try:
            with ExitStack() as stack:
                I.prepare_install_staging(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                    '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources',
                    '/root/droiddeck-owned-install-stage-proof-20261017ef-_vp8yebe', b'{}')
        except ValueError as error:
            if str(error) != 'Existing installation target or intent must never be adopted':
                raise ValueError('Exact populated-target refusal required') from None
        else:
            raise ValueError('Populated staging must refuse')
        gate.assert_not_called()
        allocation.assert_not_called()
    if targets != {path: I.A._identity(os.stat(path, follow_symlinks=False)) for path in I.H.TARGETS}:
        raise ValueError('Original installed namespace changed')
    if parents != {path: set(os.listdir(path)) for path in parents} or os.path.lexists(I.H.JOURNAL):
        raise ValueError('Refused staging changed namespace inventory')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'populated-install-staging-refusal-proof', 'existingTargetsRefused': True,
        'candidateAdmissionExecuted': False, 'intentCreated': False, 'stagingAllocationExecuted': False,
        'targetParentInventoriesUnchanged': True, 'installedTargetsUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Populated staging refusal proof incomplete; no publication or recovery performed', file=sys.stderr)
        sys.exit(1)
