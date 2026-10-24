# SPDX-License-Identifier: GPL-3.0-only
"""Retained generated combined staging proof, not a deployment command."""
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
        raise ValueError('Fixed root Linux generated combined proof required')
    spec = importlib.util.spec_from_file_location('combined_assembly_proof', Path(__file__).with_name('runtime-artifact-assembly.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    roots = {'assembly': Path('/root/droiddeck-runtime-assembly-proof-hppfgs2o/assembly'),
             'artifacts': Path('/home/hilman/droiddeck-artifact-build-output-20261015bt/artifacts')}
    credentials = os.getresuid(), os.getresgid(), tuple(os.getgroups())
    account = module.A._account('hilman')
    snapshot = {}
    with ExitStack() as stack:
        original = module.X.hold_runtime_artifact_inputs(stack, 'hilman', *roots.values())
        recipe = module._manifest({role: original['read'](role, module.A.MANIFEST).decode('utf-8') for role in roots})
        for name in module.assembly_records(recipe):
            role, relative = name.split('/', 1)
            path = roots[role] / relative
            snapshot[path, True] = module.A._identity(path.lstat())
            for parent in path.parents:
                snapshot[parent, False] = module.A._identity(parent.lstat())[:5]
        original['check']()
    # Do not overlap independent source snapshots with the publisher's held FDs.
    with ExitStack() as stack:
        parent = Path(tempfile.mkdtemp(dir='/root', prefix='droiddeck-runtime-artifact-proof-'))
        print(json.dumps({'proofRoot': str(parent), 'phase': 'new-private-publication'}, sort_keys=True), flush=True)
        result = module.publish_runtime_artifact_assembly(stack, 'hilman', *roots.values(), parent)
        result['check']()
        completed_policy = dict(result['policy'])
    del result
    with ExitStack() as stack:
        original = module.X.hold_runtime_artifact_inputs(stack, 'hilman', *roots.values())
        target = parent / module.PUBLISHED_NAME
        result = module.hold_runtime_artifact_assembly(stack, target)
        records = module.assembly_records(result['read'](module.A.MANIFEST))
        if len(records) != 234 or len(result['files']) != 235:
            raise ValueError('Exact combined assembly required')
        for name in records:
            role, relative = name.split('/', 1)
            if result['read'](name) != original['read'](role, relative):
                raise ValueError('Independent combined byte mismatch')
            before, after = (roots[role] / relative).stat(), (target / name).stat()
            if ((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
                    or (after.st_uid, after.st_gid, after.st_mode & 0o777, after.st_nlink) != (0, 0, 0o600, 1)):
                raise ValueError('Independent private combined inode required')
        original['check']()
        result['check']()
        _check_snapshot(module, snapshot)
        original['check']()
        if (module.A._account('hilman') != account
                or (os.getresuid(), os.getresgid(), tuple(os.getgroups())) != credentials):
            raise ValueError('Account or proof process credentials changed')
        print(json.dumps({**completed_policy, 'kind': 'generated-runtime-artifact-assembly-proof',
            'proofRoot': str(parent), 'independentComponentFilesVerified': 234,
            'sameSourceGeneration': True, 'sourceInputsRetained': True,
            'parentCredentialsUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Combined runtime proof refused; preserve generated proof directories', file=sys.stderr)
        sys.exit(1)
