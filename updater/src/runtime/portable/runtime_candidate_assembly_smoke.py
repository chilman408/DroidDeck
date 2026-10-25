# SPDX-License-Identifier: GPL-3.0-only
"""Retained generated assembly proof, never an installer or activation command."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def _check_snapshot(module, snapshot):
    for (path, file), before in snapshot.items():
        identity = module.A._identity(path.lstat())
        if (identity if file else identity[:5]) != before:
            raise ValueError('Original proof input generation changed')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--proof'] or sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Fixed root Linux generated assembly proof required')
    spec = importlib.util.spec_from_file_location('assembly_proof', Path(__file__).with_name('runtime-candidate-assembly.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    roots = {
        'runtime': Path('/root/droiddeck-user-candidate-stage-proof-2fu_pufj/candidate'),
        'gateway': Path('/home/hilman/droiddeck-gateway-dependencies-20261014av'),
        'display': Path('/home/hilman/droiddeck-display-dependencies-20261014av')}
    credentials = os.getresuid(), os.getresgid(), tuple(os.getgroups())
    account = module.A._account('hilman')
    snapshot = {}
    with ExitStack() as stack:
        original = module.D.hold_runtime_inputs(stack, 'hilman', *roots.values())
        for role, root in roots.items():
            raw = original['read'](role, module.A.MANIFEST)
            records = module.A.candidate_records(raw) if role == 'runtime' else module.D.dependency_records(role, raw)
            for name in (*records, module.A.MANIFEST):
                path = root / name
                snapshot[path, True] = module.A._identity(path.lstat())
                for parent in path.parents:
                    snapshot[parent, False] = module.A._identity(parent.lstat())[:5]
        original['check']()
    # Independent proof scopes must not overlap all publisher/created/verifier FDs.
    # The VM's existing 1024-descriptor limit is deliberately left unchanged.
    with ExitStack() as stack:
        parent = Path(tempfile.mkdtemp(dir='/root', prefix='droiddeck-runtime-assembly-proof-'))
        result = module.publish_runtime_assembly(stack, 'hilman', *roots.values(), parent)
        result['check']()
        completed_policy = dict(result['policy'])
    del result
    with ExitStack() as stack:
        original = module.D.hold_runtime_inputs(stack, 'hilman', *roots.values())
        target = parent / 'assembly'
        result = module.hold_runtime_assembly(stack, target)
        records = module.assembly_records(result['read'](module.A.MANIFEST))
        if len(records) != 224 or len(result['files']) != 225:
            raise ValueError('Exact coordinated assembly required')
        for name in records:
            role, relative = name.split('/', 1)
            if result['read'](name) != original['read'](role, relative):
                raise ValueError('Independent assembly byte mismatch')
            before, after = (roots[role] / relative).stat(), (target / name).stat()
            if ((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
                    or (after.st_uid, after.st_gid, after.st_mode & 0o777, after.st_nlink) != (0, 0, 0o600, 1)):
                raise ValueError('Independent private assembly inode required')
        original['check']()
        result['check']()
        _check_snapshot(module, snapshot)
        original['check']()
        if (module.A._account('hilman') != account
                or (os.getresuid(), os.getresgid(), tuple(os.getgroups())) != credentials):
            raise ValueError('Account or proof process credentials changed')
        print(json.dumps({**completed_policy, 'kind': 'generated-runtime-assembly-proof',
            'proofRoot': str(parent), 'independentComponentFilesVerified': 224,
            'assemblyFilesVerified': 225, 'sameSourceGeneration': True,
            'sourceInputsRetained': True, 'parentCredentialsUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('Runtime assembly proof refused; preserve generated proof directories', file=sys.stderr)
        sys.exit(1)
