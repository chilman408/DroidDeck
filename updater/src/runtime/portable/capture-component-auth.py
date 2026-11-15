# SPDX-License-Identifier: GPL-3.0-only
"""Authenticate complete capture candidates for staging only, never activation."""
import importlib.util
from pathlib import Path
import sys


def load(name):
    spec = importlib.util.spec_from_file_location('capture_auth_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


C = load('capture-runtime-package')
A = load('update-admission')
P = C.P
KIND = 'droiddeck-capture-runtime-attestation'
BASE_RELEASE = '1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
KEYS = {'schema', 'kind', 'channel', 'keySha256', 'bundleSha256', 'manifestSha256',
        'sourceRevision', 'runtimeVersion', 'compatibleReleaseSha256', 'contractSha256', 'purpose', 'activationAllowed'}


def statement(bundle, key_sha256):
    reviewed = C.review(bundle, P.sha(bundle))
    if reviewed['runtimeVersion'] != '0.1.1-rc.20':
        raise ValueError('Explicit reviewed base runtime version required')
    return P.encode({'schema': 1, 'kind': KIND, 'channel': 'pilot', 'keySha256': P.digest(key_sha256),
        'bundleSha256': reviewed['bundleSha256'], 'manifestSha256': reviewed['manifestSha256'],
        'sourceRevision': reviewed['sourceRevision'], 'runtimeVersion': reviewed['runtimeVersion'],
        'compatibleReleaseSha256': BASE_RELEASE, 'contractSha256': P.sha(P.encode(C.CONTRACT)),
        'purpose': 'staging-only', 'activationAllowed': False})


def review_signing_inputs(bundle, signed_statement, expected_key):
    if type(signed_statement) is not bytes or not 0 < len(signed_statement) <= 8192:
        raise ValueError('Bounded canonical capture attestation required')
    value = P.document(signed_statement)
    if (type(value) is not dict or set(value) != KEYS or type(value['schema']) is not int
            or value['schema'] != 1 or value['kind'] != KIND or value['channel'] != 'pilot'
            or value['purpose'] != 'staging-only' or value['activationAllowed'] is not False
            or value['keySha256'] != P.digest(expected_key)):
        raise ValueError('Separate nonactivating capture attestation domain required')
    # Reconstruct from fully reviewed payload: no caller-selected source/base/
    # manifest/contract field may disagree with the retained canonical package.
    if statement(bundle, expected_key) != signed_statement:
        raise ValueError('Exact complete candidate and attestation binding required')
    return {'kind': 'droiddeck-capture-signing-inputs', 'keySha256': expected_key,
        'statementSha256': P.sha(signed_statement), 'bundleSha256': P.sha(bundle),
        'purpose': 'staging-only', 'activationAllowed': False, 'payloadExecuted': False}


def authenticate(bundle, signed_statement, signature, trust_raw):
    key = A.trust(trust_raw)
    reviewed = review_signing_inputs(bundle, signed_statement, P.sha(key))
    A.signature(key, signed_statement, signature)
    return {**reviewed, 'kind': 'authenticated-capture-staging-candidate',
        'keySha256': P.sha(key), 'signatureSha256': P.sha(signature),
        'releaseStatementAuthenticated': True, 'originalInstallationOwnershipVerified': False,
        'readyForActivation': False, 'servicesChanged': False}


def main(args):
    if len(args) == 4 and args[0] == 'statement':
        raw = statement(C.read_input(args[1], C.MAX_ARCHIVE), args[2])
        C.T.write_new(Path(args[3]), raw)
        return {'kind': KIND, 'statementSha256': P.sha(raw), 'activationAllowed': False}
    if len(args) == 5 and args[0] == 'verify':
        return authenticate(C.read_input(args[1], C.MAX_ARCHIVE), C.read_input(args[2], 8192),
                            C.read_input(args[3], 64), C.read_input(args[4], 4096))
    raise ValueError('statement BUNDLE KEY_SHA256 NEW_STATEMENT or verify BUNDLE STATEMENT SIGNATURE ENROLLED_PUBLIC_TRUST')


if __name__ == '__main__':
    try: print(P.encode(main(sys.argv[1:])).decode('ascii').strip())
    except (OSError, ValueError, KeyError, TypeError):
        print('Capture candidate authentication refused; no activation performed', file=sys.stderr); sys.exit(1)
