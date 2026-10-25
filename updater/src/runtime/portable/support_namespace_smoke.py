# SPDX-License-Identifier: GPL-3.0-only
"""ONE read-only populated .215 negative preflight; no original source admission."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('actual_support_namespace', Path(__file__).with_name('runtime-support-namespace.py'))
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)


def snapshot():
    targets = (*(p / 'droiddeck' for p in N.PAM_DIRS), *(p / 'droiddeck.conf' for p in N.TMP_DIRS), *N.STATE_ROOTS)
    result = {}
    for path in targets:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            result[str(path)] = None
        else:
            result[str(path)] = N.A._identity(info)
    return result


def main():
    N.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed actual support namespace negative proof exposed')
    before, fds, limits = snapshot(), set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    if not any(value is not None for value in before.values()):
        raise ValueError('Populated support namespace required for negative proof')
    with ExitStack() as stack, mock.patch.object(N.U, 'hold_final_unit_namespace', side_effect=AssertionError('no original admission')) as source:
        try:
            N.hold_final_support_namespace(stack, 'hilman', '/root/not-opened-source', '/root/not-opened-recipes', b'{}')
        except ValueError:
            pass
        else:
            raise ValueError('Existing support namespace must refuse')
        source.assert_not_called()
    if snapshot() != before or set(os.listdir('/proc/self/fd')) != fds or resource.getrlimit(resource.RLIMIT_NOFILE) != limits:
        raise ValueError('Support namespace proof changed identities or descriptors')
    print(json.dumps({'kind': 'populated-support-namespace-negative-proof', 'refusedBeforeOriginalAdmission': True,
        'supportTargetIdentitiesUnchanged': True, 'descriptorsClosed': True, 'descriptorLimitUnchanged': True,
        'servicesChanged': False, 'privateContentRead': False, 'installationAuthorized': False}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError, TypeError, UnicodeError, N.U.subprocess.SubprocessError):
        print('Support namespace proof incomplete; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
