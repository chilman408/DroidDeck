# SPDX-License-Identifier: GPL-3.0-only
"""Read-only fixed populated-.215 refusal proof, never prepares an intent there."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('populated_intent_refusal', Path(__file__).with_name('runtime-install-intent.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def main():
    H.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed populated-VM refusal proof is exposed')
    initial = set(os.listdir('/proc/self/fd'))
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    before = {path: H.A._identity(os.stat(path, follow_symlinks=False)) for path in H.TARGETS}
    if any(not stat.S_ISDIR(os.stat(path, follow_symlinks=False).st_mode) for path in H.TARGETS):
        raise ValueError('Original populated target directories required')
    try:
        os.stat(H.JOURNAL, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError('Previously absent intent namespace required')
    with mock.patch.object(H.G, 'hold_owned_install_candidate', side_effect=AssertionError('Candidate admission must not run')) as gate:
        try:
            with ExitStack() as stack:
                H.prepare_install_intent(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                    '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources',
                    '/root/droiddeck-owned-install-stage-proof-20261017ef-_vp8yebe', b'{}')
        except ValueError as error:
            if str(error) != 'Existing installation target or intent must never be adopted':
                raise ValueError('Expected existing-target refusal required') from None
        else:
            raise ValueError('Populated installation must refuse')
        gate.assert_not_called()
    if before != {path: H.A._identity(os.stat(path, follow_symlinks=False)) for path in H.TARGETS}:
        raise ValueError('Original populated targets changed')
    try:
        os.stat(H.JOURNAL, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError('Refused proof created an intent namespace')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'populated-install-intent-refusal-proof', 'existingTargetsRefused': True,
        'candidateAdmissionExecuted': False, 'intentCreated': False, 'installedTargetsUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Populated intent refusal proof incomplete; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
