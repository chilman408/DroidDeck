# SPDX-License-Identifier: GPL-3.0-only
"""Review public aggregate signing inputs before protected custody is opened."""
import base64,importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('aggregate_public_policy',Path(__file__).with_name('aggregate-release.py'))
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
def main():
    raw=sys.stdin.buffer.read(72*1024**2+1)
    if len(raw)>72*1024**2:raise ValueError('Bounded public aggregate request required')
    value=json.loads(raw,object_pairs_hook=A.unique,parse_constant=A.invalid_constant)
    if type(value) is not dict or set(value)!={'bundle','statement','keySha256'}:raise ValueError('Closed public aggregate request required')
    bundle,statement=(base64.b64decode(value[n],validate=True) for n in ('bundle','statement'))
    print(A.encode(A.review_signing_inputs(bundle,statement,value['keySha256'])).decode('ascii').strip())
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Aggregate signing inputs refused: '+type(error).__name__,file=sys.stderr);sys.exit(1)
