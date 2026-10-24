# SPDX-License-Identifier: GPL-3.0-only
"""Read-only account-bound placement plan for the reviewed staged runtime inputs."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from types import MappingProxyType

_spec = importlib.util.spec_from_file_location('placement_source_assembly', Path(__file__).with_name('runtime-artifact-assembly.py'))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)
A = P.A
TARGET = PurePosixPath('/opt/droiddeck')
ARTIFACT_LAYOUT = MappingProxyType({
    'tesla-android-x86-touch': ('guest/bin/tesla-android-x86-touch', 0o700),
    'tesla-android-x86-api': ('guest/bin/tesla-android-x86-api', 0o700),
    'tesla-android-x86-display-latest': ('guest/bin/tesla-android-x86-display-latest', 0o700),
    'location-bridge.jar': ('guest/location-bridge.jar', 0o600),
    'logical-capture.jar': ('guest/logical-capture.jar', 0o600),
    'scrcpy-server-v4.0': ('guest/scrcpy-server-v4.0.jar', 0o600),
    'libglibutil.so.1.0.80': ('libgbinder/libglibutil.so.1.0.80', 0o644),
    'libgbinder.so.1.1.45': ('libgbinder/libgbinder.so.1.1.45', 0o644),
})
LINKS = MappingProxyType({
    'libgbinder/libglibutil.so': 'libglibutil.so.1.0.80',
    'libgbinder/libglibutil.so.1': 'libglibutil.so.1.0.80',
    'libgbinder/libgbinder.so': 'libgbinder.so.1.1.45',
    'libgbinder/libgbinder.so.1': 'libgbinder.so.1.1.45',
})
METADATA = frozenset({'assembly/bundle-manifest.json', 'assembly/runtime/bundle-manifest.json',
    'assembly/gateway/bundle-manifest.json', 'assembly/display/bundle-manifest.json', 'artifacts/bundle-manifest.json'})
FALSE_FLAGS = ('installable', 'readyForActivation', 'sourceFilesInstalled', 'dependencyFilesInstalled',
    'artifactFilesInstalled', 'dependencyCodeExecuted', 'artifactCodeExecuted', 'servicesActivated',
    'androidOperationsExecuted', 'configurationIncluded', 'frontendIncluded', 'imagesIncluded',
    'guestPayloadInstalled', 'targetNamespaceVerified')


def _account(user):
    if type(user) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
        raise ValueError('Exact existing target account required')
    account = A._account(user)
    if (min(account[1:3]) < 1000
            or re.match(r'/home/(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', account[3], re.IGNORECASE)):
        raise ValueError('Runtime-compatible unprivileged target account required')
    return account


def _plan(raw, account):
    pins = P.assembly_records(raw)
    files, counts = [], [0, 0, 0, 0]
    for source, (size, digest) in sorted(pins.items()):
        if source in METADATA:
            continue
        path, mode = None, 0o644
        for index, (prefix, destination) in enumerate((
                ('assembly/runtime/', 'runtime/'),
                ('assembly/gateway/', 'runtime/preview-gateway/node_modules/'),
                ('assembly/display/', 'runtime/waydroid-display/node_modules/'))):
            if source.startswith(prefix):
                path = destination + source.removeprefix(prefix)
                counts[index] += 1
                break
        if path is None and source.startswith('artifacts/'):
            path, mode = ARTIFACT_LAYOUT[source.removeprefix('artifacts/')]
            counts[3] += 1
        if path is None:
            raise ValueError('Closed reviewed placement source required')
        files.append({'source': source, 'path': path, 'size': size, 'sha256': digest, 'mode': mode})
    if len(pins) != 234 or counts != [76, 118, 27, 8] or not METADATA.issubset(pins):
        raise ValueError('Exact reviewed placement inventory required')
    files.sort(key=lambda row: row['path'])
    targets = {row['path'] for row in files}
    if len(targets) != 229 or targets.intersection(LINKS):
        raise ValueError('Unique regular placement destinations required')
    directories = {'': 0o755}
    for name in (*targets, *LINKS):
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                directories[str(parent)] = 0o700 if str(parent) in ('guest', 'guest/bin') else 0o755
    for name, target in LINKS.items():
        if str(PurePosixPath(name).parent / target) not in targets:
            raise ValueError('Relative alias must name a planned regular library')
    if targets.intersection(directories) or set(LINKS).intersection(directories):
        raise ValueError('Disjoint regular file, directory and alias namespaces required')
    value = {'version': 1, 'product': 'DroidDeck', 'kind': 'runtime-artifact-placement-plan',
        'targetRoot': str(TARGET), 'ownerUid': 0, 'ownerGid': 0,
        'targetAccount': dict(zip(('user', 'uid', 'gid', 'home'), account)),
        'sourceManifestSha256': hashlib.sha256(raw).hexdigest(), 'files': files,
        'directories': [{'path': name, 'mode': directories[name]} for name in sorted(directories)],
        'links': [{'path': name, 'target': LINKS[name]} for name in sorted(LINKS)],
        **dict.fromkeys(FALSE_FLAGS, False)}
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def hold_runtime_install_plan(stack, user, root):
    A._root_linux()
    source = A._canonical(root)
    if source.is_relative_to(TARGET) or TARGET.is_relative_to(source):
        raise ValueError('Staging source must be disjoint from fixed installation namespace')
    account = _account(user)
    held = P.hold_runtime_artifact_assembly(stack, source)

    def check():
        if _account(user) != account:
            raise ValueError('Target account changed during placement planning')
        held['check']()
        if _account(user) != account:
            raise ValueError('Target account changed during placement planning')

    check()
    raw = _plan(held['read'](A.MANIFEST), account)
    sources = frozenset(row['source'] for row in json.loads(raw)['files'])
    check()

    def read_plan():
        check()
        result = raw
        check()
        return result

    def read_source(name):
        if type(name) is not str or name not in sources:
            raise ValueError('Only reviewed placement payload sources may be read')
        check()
        result = held['read'](name)
        check()
        return result

    return {'check': check, 'read_plan': read_plan, 'read_source': read_source, 'policy': {
        **held['policy'], 'kind': 'held-runtime-artifact-placement-plan',
        'placementPlanVerified': True, 'targetAccountMatched': True,
        'placementFilesVerified': 229, 'placementLinksVerified': 4,
        'planSha256': hashlib.sha256(raw).hexdigest(), **dict.fromkeys(FALSE_FLAGS, False)}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != '--inspect-plan':
        raise ValueError('Only fixed read-only placement planning is exposed')
    with ExitStack() as stack:
        held = hold_runtime_install_plan(stack, argv[1], argv[2])
        held['read_plan']()
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Runtime placement planning refused; no changes performed', file=sys.stderr)
        sys.exit(1)
