# SPDX-License-Identifier: GPL-3.0-only
"""Move the original PUBLIC trust inode out of the sealed RC2 payload directory.

No key generation, replacement, enrollment, Android operation or service change.
Only the two fixed public paths are allowed. Interrupted original moves can be
completed; equal bytes in a different inode never transfer original ownership.
"""
from contextlib import ExitStack
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('relocation_trust', Path(__file__).with_name('update-trust.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
A, P = T.A, T.P
OLD = Path('/etc/droiddeck/update-trust.json')
NEW = Path('/var/lib/droiddeck/update-trust.json')
ROOT = Path('/var/lib/droiddeck/update-trust-relocation')


def read(records, path):
    raw = records.read(path, maximum=4096)
    info = os.stat(path, follow_symlinks=False)
    return raw, [info.st_dev, info.st_ino]


def move_original(old_parent, new_parent):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(old_parent, OLD.name.encode('ascii'), new_parent, NEW.name.encode('ascii'), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def relocate(expected, *, after_intent=None, after_move=None):
    base = A.load('update-base')
    base.R.A._root_linux()
    P.digest(expected)
    with ExitStack() as stack:
        records = base.Records(stack)
        parent = records.dirs.open(ROOT.parent)
        old_parent = records.dirs.open(OLD.parent)
        new_parent = records.dirs.open(NEW.parent)
        if not os.path.lexists(ROOT):
            if os.path.lexists(NEW):
                raise ValueError('Existing destination must never be adopted')
            raw, original = read(records, OLD)
            T.checked(raw, expected)
            records.check()
            os.mkdir(ROOT.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
            fd = base.H.protected(ROOT, directory=True)
            try:
                info = os.fstat(fd)
                intent = {'schema': 1, 'kind': 'original-public-trust-relocation',
                    'oldPath': str(OLD), 'newPath': str(NEW), 'keySha256': expected,
                    'trustSha256': P.sha(raw), 'originalFileIdentity': original,
                    'rootIdentity': [info.st_dev, info.st_ino]}
                base.H.write_new(fd, 'intent.json', P.encode(intent))
            finally:
                os.close(fd)
            if after_intent: after_intent()
        directory = base.H.protected(ROOT, directory=True)
        stack.callback(os.close, directory)
        import fcntl
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.callback(fcntl.flock, directory, fcntl.LOCK_UN)
        names = set(os.listdir(directory))
        if names not in ({'intent.json'}, {'intent.json', 'complete.json'}):
            raise ValueError('Only original relocation records allowed')
        # Fresh readers after a possible rename: old receipt generations are
        # not rewritten, and rename legitimately changes the trust file ctime.
        with ExitStack() as verify_stack:
            verified = base.Records(verify_stack)
            intent_raw = verified.read(ROOT / 'intent.json', maximum=4096)
            intent = P.document(intent_raw)
            info = os.fstat(directory)
            if (type(intent) is not dict or set(intent) != {'schema', 'kind', 'oldPath', 'newPath',
                    'keySha256', 'trustSha256', 'originalFileIdentity', 'rootIdentity'}
                    or type(intent['schema']) is not int or intent['schema'] != 1
                    or intent['kind'] != 'original-public-trust-relocation'
                    or intent['oldPath'] != str(OLD) or intent['newPath'] != str(NEW)
                    or intent['keySha256'] != expected
                    or intent['rootIdentity'] != [info.st_dev, info.st_ino]):
                raise ValueError('Original fixed-path relocation authority required')
            P.digest(intent['trustSha256'])
            old, new = os.path.lexists(OLD), os.path.lexists(NEW)
            if old == new or 'complete.json' in names and old:
                raise ValueError('Exactly one original public trust location required')
            current = OLD if old else NEW
            raw, identity = read(verified, current)
            T.checked(raw, expected)
            if identity != intent['originalFileIdentity'] or P.sha(raw) != intent['trustSha256']:
                raise ValueError('Same original public trust inode and bytes required')
            if 'complete.json' in names and verified.read(ROOT / 'complete.json', maximum=4096) != intent_raw:
                raise ValueError('Original relocation completion changed')
            verified.check()
        if old:
            # Linux renameat2 RENAME_NOREPLACE; source and target are fixed.
            move_original(old_parent, new_parent)
            os.fsync(old_parent)
            os.fsync(new_parent)
            if after_move: after_move()
        with ExitStack() as final_stack:
            final = base.Records(final_stack)
            current_raw, current_identity = read(final, NEW)
            T.checked(current_raw, expected)
            if (current_identity != intent['originalFileIdentity'] or P.sha(current_raw) != intent['trustSha256']
                    or os.path.lexists(OLD) or final.read(ROOT / 'intent.json', maximum=4096) != intent_raw):
                raise ValueError('Original relocated trust uncertain; preserve all records')
            final.check()
            if 'complete.json' not in names:
                base.H.write_new(directory, 'complete.json', intent_raw)
            final.check()
        return {'kind': 'original-public-trust-relocation', 'keySha256': expected,
                'trustSha256': intent['trustSha256'], 'originalPublicKeyInodePreserved': True,
                'readOnly': 'complete.json' in names, 'keyEnrolled': False, 'servicesChanged': False}


def main(args):
    if len(args) != 2 or args[0] != 'relocate-original':
        raise ValueError('Explicit fixed-path original public trust relocation required')
    print(json.dumps(relocate(args[1]), sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Original public trust relocation uncertain; preserve both fixed paths and journal', file=sys.stderr)
        sys.exit(1)
