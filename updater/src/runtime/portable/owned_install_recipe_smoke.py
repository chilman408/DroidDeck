# SPDX-License-Identifier: GPL-3.0-only
"""Fixed .215 read-only composition proof, not an installation entry point."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('recipe_proof_owned_plan', HERE / 'runtime-owned-profile-plan.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
EXPECTED = {
    'configurationDigest': '2841520938496e5dadc0cc5c4f0a867c3c3ffaf8906e66b3a08992b69b8c76e9',
    'sourceMetadataSha256': '77d9598ca2fea0e52a9c1f61dd508b526353e71b1105fc96f7de299d404c0ee3',
    'canonicalMetadataSha256': '17c2cabb6248dd3368624bc4489e19e543fca059abdd65b4c4f516295521a42e',
    'pidManifestSha256': '939582f1ddaaba2fb034fe399f842fa82026087ee4c6a982e55dabf1cf4a248c',
    'ownedHeadlessManifestSha256': 'd8a42ca6b680d2cd23dab8bbb19e2126127f460d9a717f6a6e06b6ef56aa2318',
    'ownedProfilePolicySha256': 'a05ee71563a09fa4d22cf795aa300c4d85c6219b4b8ccfac4e586c5bf8a7c76c',
}


def main():
    P.A._root_linux()
    if sys.argv[1:]:
        raise ValueError('Fixed proof inputs only')
    with ExitStack() as stack:
        name = '/etc/droiddeck/runtime.json'
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid, before.st_nlink) != (0, 0, 1)
                or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 16384):
            raise ValueError('Protected proof metadata required')
        raw = P.A._read(fd, 16384)
        if len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != EXPECTED['sourceMetadataSha256']:
            raise ValueError('Original proof metadata generation required')
        held = P.hold_owned_profile_runtime_plan(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly', raw)

        def check():
            held['check']()
            for info in (os.fstat(fd), os.stat(name, follow_symlinks=False)):
                if P.A._identity(info) != P.A._identity(before):
                    raise ValueError('Proof metadata generation changed')
            if P.A._read(fd, 16384) != raw:
                raise ValueError('Proof metadata bytes changed')
            held['check']()

        check()
        request = json.dumps({'metadata': raw.decode('utf8'), 'ownedPolicy': json.loads(held['read_owned_policy']())}).encode()
        child = subprocess.run(['/usr/bin/node', str(HERE / 'owned-install-recipe.js'), 'inspect', '-'],
            input=request, capture_output=True, timeout=10, close_fds=True, cwd='/',
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        check()
        if child.returncode != 0 or child.stderr or not 0 < len(child.stdout) <= 16384:
            raise ValueError('In-memory recipe child refused')
        summary = json.loads(child.stdout)
        if (any(summary.get(key) != value for key, value in EXPECTED.items())
                or summary.get('payloadFiles') != 20 or summary.get('manifestFiles') != 4
                or summary.get('kind') != 'owned-gpu-install-recipe'
                or summary.get('sourceRecipeCodeExecuted') is not True or summary.get('recipesDerived') is not True):
            raise ValueError('Independent recipe generations differ')
        for flag in ('filesWritten', 'rootProvenanceVerified', 'profilePolicyVerified', 'accountValidated',
                     'targetNamespaceVerified', 'gpuAccelerationVerified', 'codecOverlayVerified',
                     'independentRecoveryArmed', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted'):
            if summary.get(flag) is not False:
                raise ValueError('Inert recipe policy required')
        if (summary['serviceManifestSha256'] == 'ed6aefdbdfdaf2e22bc372757c9588dc2d6a3ebc0874480e3191b4976e859cfb'
                or summary['profileManifestSha256'] == 'f4becf1b172c823f54238022325b90e6586650e9e7c0ba0a4961068ab658ecf9'):
            raise ValueError('GPU service recipe must not masquerade as legacy package')
        check()
        print(json.dumps({'kind': 'fixed-held-owned-install-recipe-proof', 'heldInputsMatched': True,
            'configuredValidatorExecuted': True, 'sourceRecipeCodeExecuted': True,
            'ownedPlanSha256': held['policy']['planSha256'], 'recipe': summary}, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, P.configparser.Error):
        print('Owned install recipe proof refused; no files written', file=sys.stderr)
        sys.exit(1)
