# SPDX-License-Identifier: GPL-3.0-only
"""One journaled compatibility update on the original browser-owned installation."""
from contextlib import ExitStack
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('owned_browser', Path(__file__).with_name('runtime-browser-install.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
R = B.R
TARGET = '/opt/droiddeck/runtime/waydroid-display/location-adapter.js'
PIN = '9644fa287d352155dbda0a0d2732abfd2a1cfeb2849fb0d929f7a9d05c2d28e3'
JOURNAL = '/var/lib/droiddeck/runtime-compat'


def exchange(source, name, target, leaf):
    if os.fstat(source).st_dev != os.fstat(target).st_dev:
        raise ValueError('Atomic same-filesystem compatibility update required')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(source, os.fsencode(name), target, os.fsencode(leaf), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    os.fsync(source)
    os.fsync(target)


def install(authority, held, source, *, mode, prefix='/', after_intent=None):
    R.A._root_linux()
    if mode not in ('--install-new', '--resume', '--verify'):
        raise ValueError('Explicit compatibility update operation required')
    if mode != '--verify':
        R._inactive()
    base = R._plan(authority, prefix)
    plan = B.extended(base, held, prefix)
    old = plan['files'][TARGET]
    target = Path(old['mapped'])
    journal = Path(prefix) / JOURNAL.lstrip('/')
    with ExitStack() as stack:
        dirs = R._Directories(stack)
        browser, browser_pin = B.read_record(dirs, Path(prefix) / B.JOURNAL.lstrip('/') / 'intent.json')
        if browser['kind'] != 'owned-browser-extension' or browser['manifestSha256'] != B.PIN:
            raise ValueError('Original browser ownership required')
        original, original_pin = B.original(dirs, base, prefix)
        if browser['originalIntentSha256'] != original_pin:
            raise ValueError('Original browser and installation intent binding required')
        if mode == '--install-new':
            if R._objects(plan, final=True) != browser['objects']:
                raise ValueError('Original browser installation must remain unchanged')
            info = Path(source).lstat()
            _, raw = R._file(dirs, source, info.st_size, PIN, 0o600)
            if not 0 < len(raw) <= 32768:
                raise ValueError('Bounded independently pinned compatibility source required')
            parent = dirs.open(journal.parent)
            R._absent(parent, journal.name)
            os.mkdir(journal.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        txdirs, txfd = R._journal(stack, journal)
        if mode == '--install-new':
            B.write_new(txfd, 'payload', raw, 0o644)
            new = os.stat('payload', dir_fd=txfd, follow_symlinks=False)
            record = {'version': 1, 'kind': 'owned-location-compatibility', 'browserIntentSha256': browser_pin,
                'size': len(raw), 'sha256': PIN, 'device': new.st_dev, 'inode': new.st_ino}
            B.write_new(txfd, '.intent.pending', R._encode(record), 0o600)
            R.J._rename_new(txfd, '.intent.pending', 'intent.json')
            os.fsync(txfd)
            if after_intent:
                after_intent()
        record, _ = B.read_record(txdirs, journal / 'intent.json')
        if (set(record) != {'version', 'kind', 'browserIntentSha256', 'size', 'sha256', 'device', 'inode'}
                or record['version'] != 1 or record['kind'] != 'owned-location-compatibility'
                or record['browserIntentSha256'] != browser_pin or record['sha256'] != PIN
                or any(type(record[k]) is not int or record[k] <= 0 for k in ('version', 'size', 'device', 'inode'))
                or record['size'] > 32768 or set(os.listdir(txfd)) != {'intent.json', 'payload'}):
            raise ValueError('Exact compatibility intent required')
        new_identity = record['device'], record['inode']
        old_identity = browser['objects'][TARGET]['device'], browser['objects'][TARGET]['inode']
        parent = dirs.open(target.parent)
        info = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        pending = R._identity(info) == old_identity
        if not pending and R._identity(info) != new_identity:
            raise ValueError('Foreign compatibility target must not be adopted')
        if pending:
            if R._objects(plan, final=True) != browser['objects']:
                raise ValueError('Original browser objects changed before update')
            staged, _ = R._file(txdirs, journal / 'payload', record['size'], PIN, 0o644)
            if R._identity(staged) != new_identity:
                raise ValueError('Original staged compatibility object required')
            if mode == '--verify':
                raise ValueError('Compatibility publication incomplete')
            authority['check']()
            held['check']()
            R._inactive()
            exchange(txfd, 'payload', parent, target.name)
        displaced, _ = R._file(txdirs, journal / 'payload', old['size'], old['sha256'], 0o644)
        if R._identity(displaced) != old_identity:
            raise ValueError('Original displaced runtime object required')
        plan['files'][TARGET] = {**old, 'size': record['size'], 'sha256': PIN}
        expected = {**browser['objects'], TARGET: {'kind': 'files', 'device': record['device'], 'inode': record['inode']}}
        if R._objects(plan, final=True) != expected:
            raise ValueError('Updated closed installation generation changed')
        B.original(dirs, base, prefix)
        authority['check']()
        held['check']()
        return {'kind': 'owned-location-compatibility', 'originalInstallationVerified': True,
            'displacedOriginalRetained': True, 'compatibilityInstalled': True, 'servicesActivated': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 7 or args[0] not in ('--install-new', '--resume', '--verify') or args[-1] != '-':
        raise ValueError('Explicit fixed compatibility update required')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)
        held = R.A._hold_pinned_tree(stack, args[4], owner=R.A._account(args[1])[1:3], records_reader=B.records, manifest_limit=65536)
        print(json.dumps(install(authority, held, args[5], mode=args[0]), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Compatibility update incomplete; preserve original/displaced files and intent; no services activated', file=sys.stderr)
        sys.exit(1)
