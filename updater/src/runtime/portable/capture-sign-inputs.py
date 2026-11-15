# SPDX-License-Identifier: GPL-3.0-only
"""Public capture signer policy; private key input is never accepted here."""
import base64
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('capture_signing_auth', Path(__file__).with_name('capture-component-auth.py'))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)


def main():
    raw = sys.stdin.buffer.read(56*1024**2+1)
    if len(raw) > 56*1024**2: raise ValueError('Bounded public signing request required')
    value = json.loads(raw, object_pairs_hook=C.P.unique)
    if type(value) is not dict or set(value) != {'bundle', 'statement', 'keySha256'}:
        raise ValueError('Closed public capture signing request required')
    bundle, statement = (base64.b64decode(value[key], validate=True) for key in ('bundle', 'statement'))
    print(C.P.encode(C.review_signing_inputs(bundle, statement, value['keySha256'])).decode('ascii').strip())


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError, TypeError):
        print('Capture signing input review refused; no key loaded or payload executed', file=sys.stderr); sys.exit(1)
