# SPDX-License-Identifier: GPL-3.0-only
"""Read-only new materializer entry-point refusal on populated .215."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('populated_payload_refusal', Path(__file__).with_name('runtime-install-payloads.py'))
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)


def main():
    J.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed populated-VM materializer refusal is exposed')
    initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    targets = {path: J.A._identity(os.stat(path, follow_symlinks=False)) for path in J.H.TARGETS}
    parents = {path.parent: set(os.listdir(path.parent)) for path in J.H.TARGETS}
    if any(not stat.S_ISDIR(os.stat(path, follow_symlinks=False).st_mode) for path in J.H.TARGETS) or os.path.lexists(J.H.JOURNAL):
        raise ValueError('Original populated targets and absent intent required')
    with mock.patch.object(J, '_hold_snapshot_sources', side_effect=AssertionError('No source or snapshot admission')) as gate, \
            mock.patch.object(J.I, '_new_directory', side_effect=AssertionError('No allocation')) as allocation:
        try:
            with ExitStack() as stack:
                J.materialize_install_payloads(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                    '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources',
                    '/root/droiddeck-owned-install-stage-proof-20261017ef-_vp8yebe', b'{}')
        except ValueError as error:
            if str(error) != 'Existing installation target or intent must never be adopted':
                raise ValueError('Exact populated-target refusal required') from None
        else:
            raise ValueError('Populated materialization must refuse')
        gate.assert_not_called()
        allocation.assert_not_called()
    if targets != {path: J.A._identity(os.stat(path, follow_symlinks=False)) for path in J.H.TARGETS}:
        raise ValueError('Original installed identities changed')
    if parents != {path: set(os.listdir(path)) for path in parents} or os.path.lexists(J.H.JOURNAL):
        raise ValueError('Refused materialization changed namespaces')
    if set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit:
        raise ValueError('Proof descriptor lifecycle or limit changed')
    print(json.dumps({'kind': 'populated-install-payload-refusal-proof', 'existingTargetsRefused': True,
        'sourceSnapshotAdmissionExecuted': False, 'intentCreated': False, 'stagingAllocationExecuted': False,
        'payloadsCopied': False, 'targetParentInventoriesUnchanged': True, 'installedTargetsUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Populated materializer refusal proof incomplete; no final publication or recovery performed', file=sys.stderr)
        sys.exit(1)
