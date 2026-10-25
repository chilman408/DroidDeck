# SPDX-License-Identifier: GPL-3.0-only
"""Explicit public update key enrollment; no private keys or runtime mutation."""
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import os
import sys

spec = importlib.util.spec_from_file_location('trust_admission', Path(__file__).with_name('update-admission.py'))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
P = A.P


def checked(raw, expected):
    key = A.trust(raw)
    if P.sha(key) != P.digest(expected):
        raise ValueError('Separately verified public key fingerprint required')
    return key


def inspect(expected):
    base = A.load('update-base')
    base.R.A._root_linux()
    with ExitStack() as stack:
        records = base.Records(stack)
        raw = records.read(A.TRUST, maximum=4096)
        key = checked(raw, expected)
        records.check()
        return {'kind': 'droiddeck-public-trust-enrollment', 'keySha256': P.sha(key),
                'trustSha256': P.sha(raw), 'readOnly': True, 'publicKeyOnly': True,
                'servicesChanged': False, 'releaseSigned': False, 'readyForApply': False}


def enroll(source, expected):
    base = A.load('update-base')
    base.R.A._root_linux()
    with ExitStack() as stack:
        records = base.Records(stack)
        raw = records.read(Path(source), maximum=4096)
        checked(raw, expected)
        parent = records.dirs.open(A.TRUST.parent)
        records.check()
        # CreateNew is the enrollment boundary. A partial or existing trust file
        # is retained, not adopted, replaced, rotated or silently repaired.
        fd = os.open(A.TRUST.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError('Incomplete public trust enrollment')
                view = view[written:]
            os.fsync(fd)
            os.fsync(parent)
            info = os.fstat(fd)
            records.check()
            enrolled = records.read(A.TRUST, maximum=4096)
            current = os.stat(A.TRUST.name, dir_fd=parent, follow_symlinks=False)
            if (enrolled != raw or base.R.A._identity(current) != base.R.A._identity(info)):
                raise ValueError('Original public trust enrollment required')
            records.check()
        finally:
            os.close(fd)
    return {**inspect(expected), 'readOnly': False, 'newPublicKeyEnrolled': True}


def main(args):
    if len(args) == 2 and args[0] == 'inspect':
        result = inspect(args[1])
    elif len(args) == 3 and args[0] == 'enroll':
        result = enroll(args[1], args[2])
    else:
        raise ValueError('Explicit inspect or new public enrollment required; no replace or rotate')
    print(P.encode(result).decode('ascii'), end='')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, KeyError, TypeError):
        print('Public trust enrollment/inspection refused; preserve existing state', file=sys.stderr)
        sys.exit(1)
