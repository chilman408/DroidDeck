# SPDX-License-Identifier: GPL-3.0-only
"""Generated source-package handoff only; never install, execute payloads or activate."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import pwd
import sys
import tempfile


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or len(args) != 2:
        raise ValueError('Root Linux generated user-source smoke check required')
    account = pwd.getpwnam(args[1])
    if not 0 < account.pw_uid < 2**31 or not 0 < account.pw_gid < 2**31:
        raise ValueError('Independent unprivileged account required')
    source, home = Path(args[0]), Path(account.pw_dir)
    if not source.is_absolute() or not source.is_relative_to(home) or source == home:
        raise ValueError('Explicit source package under actual account home required')
    file = Path(__file__).with_name('machine-gpu-package.py')
    spec = importlib.util.spec_from_file_location('user_source_package', file)
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    owner = account.pw_uid, account.pw_gid
    with ExitStack() as stack:
        original = package.hold_source_package(stack, source, owner=owner)
        def snapshot():
            original['check']()
            result = {name: (package._identity((source / name).lstat()), original['read'](name))
                      for name in original['files']}
            original['check']()
            return result
        before = snapshot()
        with tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-user-source-smoke-') as temporary:
            parent = Path(temporary)
            if parent.parent != Path('/root') or not parent.name.startswith('droiddeck-user-source-smoke-'):
                raise ValueError('Generated root-private destination required')
            # Close held output FDs before cleanup of this generated fixture only.
            with ExitStack() as output_stack:
                output = package.publish_source_package(output_stack, source, parent, 'published', source_owner=owner)
                if output['policy']['rootPrivateSourcePackage'] is not True:
                    raise ValueError('Root-private copied package required')
                for name in output['files']:
                    info = (parent / 'published' / name).lstat()
                    if ((info.st_uid, info.st_gid, info.st_mode & 0o7777, info.st_nlink) != (0, 0, 0o600, 1)
                            or output['read'](name) != original['read'](name)):
                        raise ValueError('Independent owned byte-identical output required')
                output['check']()
                policy = dict(output['policy'])
            if before != snapshot():
                raise ValueError('Original user-source package changed')
        print(json.dumps({**policy, 'actualSourceOwnerResolved': True,
            'inputSourceUnchanged': True, 'rootOutputVerified': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, RuntimeError, KeyError):
        print('Generated user-source smoke check refused; no installation or activation performed', file=sys.stderr)
        sys.exit(1)
