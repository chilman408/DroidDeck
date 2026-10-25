# SPDX-License-Identifier: GPL-3.0-only
"""Review public release signing inputs; never load a signing key or run payloads."""
import base64
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('signing_admission', Path(__file__).with_name('update-admission.py'))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
P = A.P


def review(bundle, statement, expected_key):
    if type(statement) is not bytes or not 1 <= len(statement) <= 8192:
        raise ValueError('Bounded canonical release statement required')
    value = P.document(statement)
    if (type(value) is not dict or set(value) != {'schema', 'kind', 'channel', 'keySha256',
            'bundleSha256', 'manifestSha256', 'base', 'target', 'platform', 'dataPolicy'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'droiddeck-runtime-release' or value['channel'] != 'pilot'
            or value['keySha256'] != P.digest(expected_key) or value['dataPolicy'] != 'preserve'):
        raise ValueError('Closed pilot runtime release domain and expected signer required')
    result = P.review(bundle, value['bundleSha256'])
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(bundle), mode='r:') as archive:
        manifest = archive.extractfile(P.MANIFEST).read()
    document = P.document(manifest)
    if (P.sha(manifest) != value['manifestSha256']
            or any(value[key] != document[key] for key in ('base', 'target', 'platform', 'dataPolicy'))):
        raise ValueError('Exact reviewed release archive and manifest required')
    return {'kind': 'droiddeck-release-signing-inputs', 'statementSha256': P.sha(statement),
            'bundleSha256': result['bundleSha256'], 'manifestSha256': P.sha(manifest),
            'keySha256': expected_key, 'payloadExecuted': False, 'releaseSigned': False}


def main():
    # Public bytes only through stdin; no private key/password input is accepted.
    raw = sys.stdin.buffer.read(115 * 1024 * 1024 + 1)
    if len(raw) > 115 * 1024 * 1024:
        raise ValueError('Bounded public signing request required')
    import json
    request = json.loads(raw, object_pairs_hook=P.unique)
    if type(request) is not dict or set(request) != {'bundle', 'statement', 'keySha256'}:
        raise ValueError('Closed public signing request required')
    bundle, statement = (base64.b64decode(request[key], validate=True) for key in ('bundle', 'statement'))
    print(P.encode(review(bundle, statement, request['keySha256'])).decode('ascii'), end='')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print('Release signing input review refused; no key loaded or payload executed', file=sys.stderr)
        sys.exit(1)
