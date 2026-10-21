# SPDX-License-Identifier: GPL-3.0-only
"""Generated-source fixture proof only; never an installer or service launcher."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--proof'] or sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Fixed root Linux generated proof required')
    spec = importlib.util.spec_from_file_location('user_stage_proof', Path(__file__).with_name('runtime-candidate-stage.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = Path('/home/hilman/droiddeck-runtime-candidate-20261016dp-o86f2Odk/candidate-one')
    credentials = os.getresuid(), os.getresgid(), tuple(os.getgroups())
    account = module.A._account('hilman')
    with ExitStack() as stack:
        original = module.A.hold_user_candidate(stack, 'hilman', source)
        root = Path(tempfile.mkdtemp(dir='/root', prefix='droiddeck-user-candidate-stage-proof-'))
        result = module.publish_user_candidate(stack, 'hilman', source, root)
        destination = root / 'candidate'
        pins = module.A.candidate_records(result['read'](module.A.MANIFEST))
        for name in result['files']:
            raw = result['read'](name)
            if raw != original['read'](name):
                raise ValueError('Independent source/copy byte mismatch')
            first, second = (source / name).stat(), (destination / name).stat()
            if ((first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
                    or (second.st_uid, second.st_gid, second.st_mode & 0o777) != (0, 0, 0o600)):
                raise ValueError('Independent root-private copy required')
            if name != module.A.MANIFEST and (len(raw), hashlib.sha256(raw).hexdigest()) != pins[name]:
                raise ValueError('Independently pinned copied bytes required')
        original['check']()
        result['check']()
        if (module.A._account('hilman') != account
                or (os.getresuid(), os.getresgid(), tuple(os.getgroups())) != credentials):
            raise ValueError('Account or proof process credentials changed')
        print(json.dumps({**result['policy'], 'kind': 'generated-user-candidate-stage-proof',
                          'proofRoot': str(root), 'independentPayloadsVerified': len(pins),
                          'sameSourceGeneration': True, 'sourceInputRetained': True,
                          'parentCredentialsUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        print('User candidate staging proof refused; preserve generated proof directories', file=sys.stderr)
        sys.exit(1)
