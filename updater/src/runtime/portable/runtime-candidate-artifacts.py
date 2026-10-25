# SPDX-License-Identifier: GPL-3.0-only
"""Hold reviewed runtime assembly and reproducible build inputs; never execute them."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from types import MappingProxyType

_spec = importlib.util.spec_from_file_location('artifact_input_assembly', Path(__file__).with_name('runtime-candidate-assembly.py'))
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)
A = W.A

# Independently accepted bt and deterministic fl/fo build manifests, unchanged aq payload pins.
MANIFEST_SHA256 = 'fbf7376b8dac11dddadc6a329190167e879b2cf4d459ebdd25b438573fbf4f3e'
REPRODUCIBLE_MANIFEST_SHA256 = '74b302c76b441686b2fab3b001928718c97b3dd77bad52e9a4219560ad037ff2'
ARTIFACT_PINS = MappingProxyType({
    'tesla-android-x86-touch': (991688, 'bf37f6b9c94446b037d352446c77035754d0e7a0fccb62a1d59da1a9c4cd79dd'),
    'tesla-android-x86-api': (3149808, '7a938269a9437e94e84bd9d97727c6283c511cdb6dfce300ccce7bbb94879754'),
    'tesla-android-x86-display-latest': (1006488, 'e90c13b4ee680b9fb4971b1701e8bc124b35a07996f5863cc98653b25db5d7d8'),
    'location-bridge.jar': (4973, '809acb4b5d4c86ece5d2378dc3dd9eb2b47bc4781d756012047570c61a493cbc'),
    'logical-capture.jar': (3114, 'a12fa7ecdb27dbbd63656b8dea1b2f9e12d2aae75632b73afe86ff2d2761c044'),
    'libglibutil.so.1.0.80': (85664, 'adf21a840c7754cb61f82e444ae73c1f410abe4f1427e35f74fe45fa99170d90'),
    'libgbinder.so.1.1.45': (164192, 'd473613f9c57921113d3cefe1cbc99676476270db756b52b5aa407c1ece06cae'),
    'scrcpy-server-v4.0': (732226, '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a'),
})


def artifact_records(raw):
    if (type(raw) is not bytes or not 0 < len(raw) <= 65536
            or hashlib.sha256(raw).hexdigest() not in (MANIFEST_SHA256, REPRODUCIBLE_MANIFEST_SHA256)):
        raise ValueError('Independently pinned artifact build manifest required')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'version', 'product', 'kind', 'installable',
            'servicesActivated', 'androidOperationsExecuted', 'files', 'buildReceipts'}
            or type(value['version']) is not int or value['version'] != 1 or value['product'] != 'DroidDeck'
            or value['kind'] != 'runtime-artifact-build-component' or value['installable'] is not False
            or value['servicesActivated'] is not False or value['androidOperationsExecuted'] is not False
            or type(value['files']) is not list or len(value['files']) != len(ARTIFACT_PINS)):
        raise ValueError('Closed noninstallable artifact recipe required')
    records = {}
    for row in value['files']:
        if (type(row) is not dict or set(row) != {'path', 'size', 'sha256'}
                or type(row['path']) is not str or row['path'] in records
                or type(row['size']) is not int or type(row['sha256']) is not str
                or ARTIFACT_PINS.get(row['path']) != (row['size'], row['sha256'])):
            raise ValueError('Independent artifact payload pin required')
        records[row['path']] = row['size'], row['sha256']
    if set(records) != set(ARTIFACT_PINS):
        raise ValueError('Closed artifact inventory required')
    return ARTIFACT_PINS


def hold_artifact_bundle(stack, root, *, owner=(0, 0)):
    return A._hold_pinned_tree(stack, root, owner=owner, records_reader=artifact_records, manifest_limit=65536)


def hold_runtime_artifact_inputs(stack, user, assembly, artifacts):
    A._root_linux()
    if type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
        raise ValueError('Exact unprivileged artifact source account required')
    assembly, artifacts = map(A._canonical, (assembly, artifacts))
    if assembly.is_relative_to(artifacts) or artifacts.is_relative_to(assembly):
        raise ValueError('Disjoint assembly and artifact namespaces required')
    account = A._account(user)
    home = PurePosixPath(account[3])
    if artifacts == home or not artifacts.is_relative_to(home):
        raise ValueError('Artifact bundle below independently resolved account home required')
    inputs = {'assembly': W.hold_runtime_assembly(stack, assembly)}
    if A._account(user) != account:
        raise ValueError('Artifact source account changed')
    inputs['artifacts'] = hold_artifact_bundle(stack, artifacts, owner=account[1:3])

    def check():
        if A._account(user) != account:
            raise ValueError('Artifact source account changed')
        for held in inputs.values():
            held['check']()
        if A._account(user) != account:
            raise ValueError('Artifact source account changed')

    def read(role, relative):
        if type(role) is not str or role not in inputs:
            raise ValueError('Closed runtime/artifact input role required')
        check()
        raw = inputs[role]['read'](relative)
        check()
        return raw

    check()
    return {'check': check, 'read': read, 'policy': {
        **inputs['assembly']['policy'], 'kind': 'coordinated-runtime-artifact-inputs',
        'coordinatedRuntimeArtifactInputsVerified': True, 'sourceAccountMatched': True,
        'artifactFilesVerified': 8, 'inputFilesVerified': 234,
        'rootPrivateArtifactCopyVerified': False, 'artifactFilesInstalled': False,
        'artifactCodeExecuted': False}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4 or argv[0] != '--verify-inputs':
        raise ValueError('Only read-only fixed runtime/artifact admission is available')
    with ExitStack() as stack:
        held = hold_runtime_artifact_inputs(stack, *argv[1:])
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Runtime artifact admission refused; no changes performed', file=sys.stderr)
        sys.exit(1)
