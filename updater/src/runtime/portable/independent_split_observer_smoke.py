# SPDX-License-Identifier: GPL-3.0-only
"""ONE read-only snapshot of preserved ez private roles; never replays publisher."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('actual_split_observer', Path(__file__).with_name('runtime-independent-split-observer.py'))
Y = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Y)
O = Y.O
RUNTIME = Path('/root/droiddeck-split-runtime-ez-8odqunw7')
CONFIG = Path('/run/droiddeck-split-config-ez-vlfir39l')
JOURNAL = Path('/root/droiddeck-publication-journal-ez-5gpkiete')
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'
ALLOCATION = '3978ff67de9c00591d5611d58089f04f127dc276eaa8f2affd1c71ba419d5f68'


def _metadata():
    result = {}
    for root in (RUNTIME, CONFIG, JOURNAL):
        result[str(root)] = O.A._identity(root.lstat())
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in (*folders, *files):
                path = Path(directory) / name
                result[str(path)] = O.A._identity(path.lstat())
    return result


def main():
    O.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed read-only split inspection proof exposed')
    initial, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    before = _metadata()
    installed = {path: O.A._identity(os.stat(path, follow_symlinks=False)) for path in O.K.ROOTS}
    counts = {'freshOriginalHolds': 0, 'freshLayoutReads': 0, 'originalPayloadReads': 0}
    actual = Y.K.hold_final_install_layout

    def fresh(*args):
        counts['freshOriginalHolds'] += 1
        original = actual(*args)
        plan, read = original['read_plan'], original['read_file']
        def layout():
            raw = plan()
            counts['freshLayoutReads'] += 1
            if O.E._sha(raw) != LAYOUT:
                raise ValueError('Exact original layout generation required')
            return raw
        def payload(name):
            counts['originalPayloadReads'] += 1
            return read(name)
        return {**original, 'read_plan': layout, 'read_file': payload}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        baseline = os.fstat(fd)
        if (not stat.S_ISREG(baseline.st_mode) or (baseline.st_uid, baseline.st_gid, baseline.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(baseline.st_mode) != 0o644 or not 0 < baseline.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = O.A._read(fd, 16384)
        if O.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original machine metadata generation required')
        with mock.patch.object(Y.K, 'hold_final_install_layout', side_effect=fresh):
            held = Y.hold_independent_split_roles(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources',
                str(RUNTIME), str(CONFIG), str(JOURNAL), raw)
        report = held['observe']()
        if (report['allocationSha256'] != ALLOCATION or report['finalLayoutSha256'] != LAYOUT
                or counts != {'freshOriginalHolds': 1, 'freshLayoutReads': 1, 'originalPayloadReads': 0}
                or (report['privateRegularFilesVerified'], report['privateAliasesVerified']) != (261, 4)
                or not report['privateDistinctDevicesObserved']):
            raise ValueError('Independent split snapshot/original generation mismatch')
        if any(report[key] for key in (*O.J.FALSE_FLAGS, 'storedReceiptAdopted', 'createdInodeOwnershipVerified',
                'privateTargetOwnershipVerified', 'privateCrossDeviceRolePublicationVerified',
                'crossFilesystemPublicationVerified', 'globallyAtomic', 'persistentConfigDestinationVerified',
                'restartRecoveryVerified', 'powerLossRecoveryVerified', 'artifactBytesCopied')):
            raise ValueError('Read-only split inspection cannot acquire install/recovery authority')
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if O.A._identity(info) != O.A._identity(baseline):
                raise ValueError('Original machine metadata changed')
        if O.A._read(fd, 16384) != raw:
            raise ValueError('Original machine metadata bytes changed')
        if len(os.listdir('/proc/self/fd')) > report['independentSplitDescriptorUpperBound']:
            raise ValueError('Independent split descriptor budget exceeded')
    if (_metadata() != before or installed != {path: O.A._identity(os.stat(path, follow_symlinks=False)) for path in O.K.ROOTS}
            or set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limits):
        raise ValueError('Read-only snapshot metadata/installed/FD/limit invariants changed')
    print(json.dumps({'kind': 'independent-private-split-inspection-proof', **counts,
        'privateTreeMetadataEntriesUnchanged': len(before), 'installedIdentitiesUnchanged': True,
        'descriptorLimitUnchanged': True, 'descriptorsClosed': True, 'publisherReplayed': False,
        'privatePayloadBytesTransported': False, 'inspection': report}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError,
            O.T.R.M.subprocess.SubprocessError, O.E.P.configparser.Error):
        print('Read-only independent split proof refused; preserve state; no installation performed', file=sys.stderr)
        sys.exit(1)
