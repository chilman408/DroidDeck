# SPDX-License-Identifier: GPL-3.0-only
"""Public activation signing policy. No private key inputs or worker execution."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
spec=importlib.util.spec_from_file_location('activation_signing',Path(__file__).with_name('capture-activation-auth.py'))
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)


def main():
    raw=sys.stdin.buffer.read(64*1024**2+1)
    if len(raw)>64*1024**2:raise ValueError('Bounded public activation signing request required')
    value=json.loads(raw,object_pairs_hook=A.P.unique)
    if type(value) is not dict or set(value)!={'bundle','statement','keySha256'}:raise ValueError('Closed public activation inputs required')
    bundle,statement=(base64.b64decode(value[n],validate=True) for n in ('bundle','statement'))
    print(A.P.encode(A.review_signing_inputs(bundle,statement,value['keySha256'])).decode('ascii').strip())


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Activation signing inputs refused: '+type(error).__name__,file=sys.stderr);sys.exit(1)
