# SPDX-License-Identifier: GPL-3.0-only
"""ONE fixed read-only installed package consistency proof; no source admission."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys

spec = importlib.util.spec_from_file_location('actual_host_package_files', Path(__file__).with_name('runtime-host-package-files.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def main():
    H.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Only fixed read-only installed host package proof exposed')
    before = {str(name): H.A._identity(os.lstat(name)) for name in (*H.FILES, H.ALIAS)}
    fds, limits = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
    with ExitStack() as stack:
        held = H.hold_host_package_files(stack)
        held['check']()
        if held['policy']['hostPackageFilesObserved'] != 3:
            raise ValueError('Exact three installed host files required')
        policy = held['policy']
    if (before != {str(name): H.A._identity(os.lstat(name)) for name in (*H.FILES, H.ALIAS)}
            or set(os.listdir('/proc/self/fd')) != fds or resource.getrlimit(resource.RLIMIT_NOFILE) != limits):
        raise ValueError('Host package identities or descriptor lifecycle changed')
    print(json.dumps({'kind': 'installed-host-package-file-proof', 'hostFileIdentitiesUnchanged': True,
        'descriptorsClosed': True, 'descriptorLimitUnchanged': True, 'servicesChanged': False,
        'artifactBytesCopied': False, 'observation': policy}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, H.subprocess.SubprocessError):
        print('Host package proof incomplete; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
