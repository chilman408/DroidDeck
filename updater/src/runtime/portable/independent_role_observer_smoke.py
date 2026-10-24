# SPDX-License-Identifier: GPL-3.0-only
"""ONE read-only inspection of ew proof in place; never reruns its publisher."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('actual_independent_roles', Path(__file__).with_name('runtime-independent-role-observer.py'))
O = importlib.util.module_from_spec(spec)
spec.loader.exec_module(O)
PARENT = Path('/root/droiddeck-owned-publication-ew-_dgp3xk6')
JOURNAL = Path('/root/droiddeck-publication-journal-ew-ykrkdx5r')
LAYOUT = '2d1e2fac35d209d2b7fcd86c11ce405eba1beb949d82ae08580e2c39ed6571d4'
ALLOCATION = 'b733c4f4cac5c27f6fe6a400af51e5a573cee39527870292332cafef6e1730f4'


def _metadata():
    result = {}
    for root in (PARENT, JOURNAL):
        result[str(root)] = O.A._identity(root.lstat())
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in (*folders, *files):
                path = Path(directory) / name
                result[str(path)] = O.A._identity(path.lstat())
    return result


def main():
    O.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed read-only independent role proof exposed')
    initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    before = _metadata()
    installed = {path: O.A._identity(os.stat(path, follow_symlinks=False)) for path in O.K.ROOTS}
    counts = {'freshOriginalHolds': 0, 'freshLayoutReads': 0, 'originalPayloadReads': 0}
    admit = O.K.hold_final_install_layout

    def fresh(*args):
        counts['freshOriginalHolds'] += 1
        held = admit(*args)
        plan, read = held['read_plan'], held['read_file']
        def layout():
            value = plan()
            counts['freshLayoutReads'] += 1
            if O.E._sha(value) != LAYOUT:
                raise ValueError('Original fixed layout generation required')
            return value
        def payload(name):
            counts['originalPayloadReads'] += 1
            return read(name)
        return {**held, 'read_plan': layout, 'read_file': payload}

    with ExitStack() as stack:
        path = '/etc/droiddeck/runtime.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        original = os.fstat(fd)
        if (not stat.S_ISREG(original.st_mode) or (original.st_uid, original.st_gid, original.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(original.st_mode) != 0o644 or not 0 < original.st_size <= 16384):
            raise ValueError('Original machine metadata required')
        raw = O.A._read(fd, 16384)
        if O.E._sha(raw) != '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3':
            raise ValueError('Original machine metadata generation required')
        with mock.patch.object(O.K, 'hold_final_install_layout', side_effect=fresh):
            held = O.hold_independent_roles(stack, 'hilman',
                '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly',
                '/root/droiddeck-owned-install-plan-20261017ee/recipe-sources', str(PARENT), str(JOURNAL), raw)
        report = held['observe']()
        if (report['allocationSha256'] != ALLOCATION or report['finalLayoutSha256'] != LAYOUT
                or counts != {'freshOriginalHolds': 1, 'freshLayoutReads': 1, 'originalPayloadReads': 0}
                or (report['privateRegularFilesVerified'], report['privateAliasesVerified']) != (261, 4)):
            raise ValueError('Independent snapshot/original generation mismatch')
        if any(report[key] for key in (*O.J.FALSE_FLAGS, 'storedReceiptAdopted', 'createdInodeOwnershipVerified',
                                      'privateTargetOwnershipVerified', 'restartRecoveryVerified', 'artifactBytesCopied')):
            raise ValueError('Read-only inspection cannot acquire install/recovery authority')
        for info in (os.fstat(fd), os.stat(path, follow_symlinks=False)):
            if O.A._identity(info) != O.A._identity(original):
                raise ValueError('Original settings changed')
        if O.A._read(fd, 16384) != raw:
            raise ValueError('Original settings bytes changed')
    if (_metadata() != before or installed != {path: O.A._identity(os.stat(path, follow_symlinks=False)) for path in O.K.ROOTS}
            or set(os.listdir('/proc/self/fd')) != initial or resource.getrlimit(resource.RLIMIT_NOFILE) != limit):
        raise ValueError('Read-only proof metadata/FD/limit invariant changed')
    print(json.dumps({'kind': 'independent-private-role-inspection-proof', **counts,
        'privateTreeMetadataEntriesUnchanged': len(before), 'installedIdentitiesUnchanged': True,
        'descriptorLimitUnchanged': True, 'descriptorsClosed': True, 'publisherReplayed': False,
        'privatePayloadBytesTransported': False, 'inspection': report}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, O.T.R.M.subprocess.SubprocessError, O.E.P.configparser.Error):
        print('Read-only independent role proof refused; preserve state; no installation performed', file=sys.stderr)
        sys.exit(1)
