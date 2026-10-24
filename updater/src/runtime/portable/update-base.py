# SPDX-License-Identifier: GPL-3.0-only
"""Read-only RC2 ownership proof; no update admission, publication or activation."""
import ast
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

import importlib.util


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


I = load('install-runtime')
C, B, R = I.C, I.B, I.R
S = load('stage-installer-sources')
H = S.H
STATE = Path('/root/droiddeck-installation')
SOURCE_REVISION = 'c0e3a5a4a6b1e41647159034227ea08b8ff6c139'
PACKAGE_PIN = '6996c754fe58aa1ce9e62426bd9a60aefbc78089b66e2d65eeec6ca5241c9b20'
SOURCE_PIN = 'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28'
COMPONENTS = {'frontend': '/opt/droiddeck/frontend',
              'gateway': '/opt/droiddeck/runtime/preview-gateway',
              'display': '/opt/droiddeck/runtime/waydroid-display'}


def canonical(raw):
    value = json.loads(raw)
    if H.encode(value) != raw:
        raise ValueError('Original canonical installation record required')
    return value


def phase_names(source):
    names = [node.args[0].value for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == 'phase' and node.args and isinstance(node.args[0], ast.Constant)
             and isinstance(node.args[0].value, str)]
    if len(names) != 35 or len(set(names)) != 35:
        raise ValueError('Pinned complete RC2 phase inventory required')
    return sorted(names)


def completed(intent, complete, config, machine_pin):
    if (type(intent.get('schema')) is not int
            or intent != {'schema': 1, 'configuration': config,
                   'packageManifestSha256': PACKAGE_PIN, 'machineSha256': machine_pin}
            or set(complete) != {'kind', 'transaction', 'browserRebootRecoveryAcceptanceRequired', 'installable'}
            or complete['kind'] != 'clean-machine-installation'
            or complete['browserRebootRecoveryAcceptanceRequired'] is not True
            or complete['installable'] is not False
            or not isinstance(complete['transaction'], str)
            or not re.fullmatch('[0-9a-f]{12}', complete['transaction'])):
        raise ValueError('Original completed RC2 machine installation required')


class Records:
    def __init__(self, stack):
        self.dirs, self.files = R._Directories(stack), {}

    def read(self, path, mode=0o600, maximum=1048576):
        path = Path(path)
        parent = self.dirs.open(path.parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                    or stat.S_IMODE(before.st_mode) != mode or not 0 < before.st_size <= maximum):
                raise ValueError('Bounded protected single-link receipt required')
            raw = R.A._read(fd, before.st_size)
            for current in (os.fstat(fd), os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
                if R.A._identity(current) != R.A._identity(before):
                    raise ValueError('Receipt changed while reading')
        finally:
            os.close(fd)
        info, _ = R._file(self.dirs, path, len(raw), S.digest(raw), mode)
        if R.A._identity(info) != R.A._identity(before):
            raise ValueError('Original observed receipt identity required')
        record = (len(raw), S.digest(raw), mode, R.A._identity(info))
        if str(path) in self.files and self.files[str(path)] != record:
            raise ValueError('Previously observed receipt cannot be replaced')
        self.files[str(path)] = record
        return raw

    def check(self):
        for path, (size, pin, mode, identity) in self.files.items():
            info, _ = R._file(self.dirs, path, size, pin, mode)
            if R.A._identity(info) != identity:
                raise ValueError('Original installation receipt generation changed')
        self.dirs.check()


def owned_inventory(authority, held, compat, *, prefix='/', guard=lambda: None):
    # Reuse original registration/browser/compat authorities, including displaced
    # objects. Equal bytes in a foreign inode do not transfer ownership.
    guard()
    C.install(authority, held, compat, mode='--verify', prefix=prefix)
    plan = B.extended(R._plan(authority, prefix), held, prefix)
    with ExitStack() as stack:
        dirs = R._Directories(stack)
        record, _ = B.read_record(dirs, Path(prefix) / C.JOURNAL.lstrip('/') / 'intent.json')
        browser, _ = B.read_record(dirs, Path(prefix) / B.JOURNAL.lstrip('/') / 'intent.json')
        plan['files'][C.TARGET] = {**plan['files'][C.TARGET], 'size': record['size'], 'sha256': C.PIN}
        expected = {**browser['objects'], C.TARGET:
                    {'kind': 'files', 'device': record['device'], 'inode': record['inode']}}
        objects = R._objects(plan, final=True)
        if objects != expected:
            raise ValueError('Original complete installation identities required')
        components = []
        for name, root in sorted(COMPONENTS.items()):
            files = []
            for path, row in sorted(plan['files'].items()):
                if PurePosixPath(path).is_relative_to(PurePosixPath(root)):
                    files.append({'path': str(PurePosixPath(path).relative_to(root)),
                                  **{key: row[key] for key in ('size', 'sha256', 'mode')}})
            if not files or root not in objects or objects[root]['kind'] != 'directories':
                raise ValueError('All three fixed runtime component inventories required')
            components.append({'name': name, 'root': root, 'files': files})
        guard()
        C.install(authority, held, compat, mode='--verify', prefix=prefix)
        if R._objects(plan, final=True) != objects:
            raise ValueError('Installation changed during ownership inspection')
        return {'components': components, 'objects': objects, 'placementPlanSha256': plan['sha256']}


def inspect(package, *, _tail_token=None):
    R.A._root_linux()
    package = Path(package)
    if not package.is_absolute() or str(package) != str(R.A._canonical(str(package))):
        raise ValueError('Canonical root-protected baseline package required')
    with ExitStack() as stack:
        records = Records(stack)
        raw_manifest = records.read(package / 'package-manifest.json', mode=0o644)
        if S.digest(raw_manifest) != PACKAGE_PIN or json.loads(raw_manifest)['sourceRevision'] != SOURCE_REVISION:
            raise ValueError('Exact independently pinned RC2 baseline package required')
        sources, pin = S.read_package(package)
        if pin != PACKAGE_PIN:
            raise ValueError('Baseline source package changed')
        check_lock = I.I.lock(stack)
        raw = records.read(STATE / 'configuration.json')
        config = canonical(raw)
        machine_pin = S.digest(records.read('/etc/machine-id', mode=0o444, maximum=128))
        phases = phase_names(sources['runtime/portable/install-machine.py'])
        tail = None
        if _tail_token is None:
            completed(canonical(records.read(STATE / 'intent.json')),
                      canonical(records.read(STATE / 'complete.json')), config, machine_pin)
            markers = {name + suffix for name in phases for suffix in ('.started.json', '.complete.json')}
            if {name for name in os.listdir(records.dirs.open(STATE))
                    if name.endswith(('.started.json', '.complete.json'))} != markers:
                raise ValueError('All and only original installation phase receipts required')
            for name in phases:
                for suffix in ('.started.json', '.complete.json'):
                    if records.read(STATE / (name + suffix)) != H.encode({'phase': name}):
                        raise ValueError('Complete original phase receipt required')
        else:
            # A separate audit is NOT a completed base until independently sealed.
            tail = load('installation-tail-recovery')
            boundary = tail.boundary(records, phases, config, machine_pin, _tail_token)
        user = config['user']
        authority = R.K.hold_final_install_layout(stack, user,
            STATE / 'protected/artifact-stage/assembly', S.ROOT / 'recipes', raw)
        account = R.A._account(user)
        held = R.A._hold_pinned_tree(stack, Path(account[3]) / 'droiddeck-installation/prepared/frontend',
                                   owner=account[1:3], records_reader=B.records, manifest_limit=65536)
        compat = S.ROOT / 'compat-source.js'
        records.read(compat, maximum=32768)
        for journal in (R.JOURNAL, B.JOURNAL, C.JOURNAL, I.S.JOURNAL, I.S.RESET_JOURNAL):
            records.read(Path(journal) / 'intent.json')

        def guard():
            check_lock()
            records.check()

        I.finish(authority, held, compat, mode='--verify-runtime', guard=guard)
        inventory = owned_inventory(authority, held, compat, guard=guard)
        if tail:
            recovered = tail.live(records, boundary, config)
        guard()
        result = {'kind': 'installed-update-base-inspection', 'base': {'version': '0.1.0-rc.2',
                'sourceSha256': SOURCE_PIN}, **inventory,
                'originalInstallationOwnershipVerified': True, 'completedPhasesVerified': len(phases),
                'configurationSha256': S.digest(raw), 'readOnly': True, 'installable': False,
                'durableAdmissionCreated': False, 'targetReleaseAuthenticated': False,
                'servicesChanged': False, 'androidDataRead': False, 'tlsPrivateKeyRead': False}
        result['receiptGenerations'] = ({name: list(row[3]) for name, row in sorted(records.files.items())}
                                       if tail is None else tail.receipt_generations(records, _tail_token))
        if tail:
            result.update(kind='installation-tail-audit', completedPhasesVerified=31,
                          installationCompletionVerified=False, recoveredTailPhasesVerified=4,
                          tailAuthority={**boundary, **recovered, 'inventory': inventory,
                                         'receiptGenerations': tail.receipt_generations(records, _tail_token)})
        return result


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 2 or args[0] not in ('inspect', 'inspect-recovered'):
        raise ValueError('inspect or inspect-recovered ABSOLUTE_PINNED_RC2_PACKAGE required')
    result = inspect(args[1]) if args[0] == 'inspect' else load('installation-tail-recovery').verify(args[1])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Installed RC2 ownership unverified; no files or services changed', file=sys.stderr)
        sys.exit(1)
