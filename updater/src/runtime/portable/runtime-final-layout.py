# SPDX-License-Identifier: GPL-3.0-only
"""Read-only final placement metadata derived from independently admitted inputs."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('final_layout_owned_stage', Path(__file__).with_name('runtime-owned-install-stage.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
E, A, L = F.E, F.A, F.E.P.C.L
ROOTS = ('/opt/droiddeck', '/etc/droiddeck')
REGISTRATIONS = MappingProxyType({
    **{'services/' + name: '/etc/' + name for name in E.SERVICE_FILES},
    'pid-compat/systemd/40-droiddeck-pid-compat.conf': '/etc/systemd/system/waydroid-container.service.d/40-droiddeck-pid-compat.conf',
    'headless-owned-runtime/systemd/60-droiddeck-headless.conf': '/etc/systemd/system/tesla-waydroid-headless-session.service.d/60-droiddeck-headless.conf',
})


def _derive(plan):
    F._layout(plan)
    value = json.loads(plan)
    if (E._encode(value, compact=True) != plan or type(value.get('version')) is not int
            or value['version'] != 1 or value.get('product') != 'DroidDeck'
            or type(value.get('ownerUid')) is not int or type(value.get('ownerGid')) is not int
            or (value['ownerUid'], value['ownerGid']) != (0, 0)):
        raise ValueError('Canonical independently admitted root-owned plan required')
    links = [{'path': name, 'target': target} for name, target in sorted(L.LINKS.items())]
    if value['links'] != links:
        raise ValueError('Exact reviewed relative library aliases required')
    files, readers = [], {}

    def add(destination, role, staged, source_kind, original, size, pin, mode, registration=False):
        if destination in readers:
            raise ValueError('Unique final destination required')
        files.append({'path': destination, 'sourceRole': role, 'stagedPath': staged,
            'size': size, 'sha256': pin, 'mode': mode, 'uid': 0, 'gid': 0,
            'registration': registration})
        readers[destination] = (source_kind, original, size, pin)

    artifact_modes = {path: mode for path, mode in L.ARTIFACT_LAYOUT.values()}
    seen_artifacts = set()
    for row in value['files']:
        name = row['path']
        expected = artifact_modes.get(name)
        if expected is not None:
            seen_artifacts.add(name)
        elif name.startswith('runtime/'):
            expected = 0o644
        else:
            raise ValueError('Closed runtime or reviewed artifact destination required')
        if type(row['mode']) is not int or row['mode'] != expected:
            raise ValueError('Reviewed runtime and artifact modes required')
        add(ROOTS[0] + '/' + name, 'opt', name, 'runtime', row['source'], row['size'], row['sha256'], expected)
    if seen_artifacts != set(artifact_modes):
        raise ValueError('All eight reviewed artifacts required')
    for row in value['ownedRecipeFiles']:
        name = row['path']
        if name.startswith(('pid-compat/', 'headless-owned-runtime/')):
            destination = ROOTS[0] + '/' + name
        else:
            destination = ROOTS[1] + '/' + name
        add(destination, 'profile', 'recipe/' + name, 'recipe', name, row['size'], row['sha256'], 0o644)
        if name in REGISTRATIONS:
            add(REGISTRATIONS[name], 'profile', 'recipe/' + name, 'recipe', name,
                row['size'], row['sha256'], 0o644, registration=True)
    names = set(readers)
    if len(files) != 259 or sum(row['registration'] for row in files) != 9:
        raise ValueError('Complete 250 owned files and nine registrations required')
    final_links = [{'path': ROOTS[0] + '/' + row['path'], 'target': row['target'], 'uid': 0, 'gid': 0} for row in links]
    for link in final_links:
        if str(PurePosixPath(link['path']).parent / link['target']) not in names or link['path'] in names:
            raise ValueError('Relative alias must resolve directly to a planned regular library')
    directories = {root: 0o755 for root in ROOTS}
    for name in (*names, *(row['path'] for row in final_links)):
        for parent in PurePosixPath(name).parents:
            if any(str(parent) == root or parent.is_relative_to(PurePosixPath(root)) for root in ROOTS):
                directories[str(parent)] = 0o700 if str(parent) in (ROOTS[0] + '/guest', ROOTS[0] + '/guest/bin') else 0o755
        if any(str(parent) in names for parent in PurePosixPath(name).parents):
            raise ValueError('Final files must not collide with ancestor directories')
    if names.intersection(directories) or any(row['path'] in directories for row in final_links):
        raise ValueError('Disjoint final file/directory/link namespaces required')
    result = {'version': 1, 'product': 'DroidDeck', 'kind': 'final-install-placement-plan',
        'ownedInstallPlanSha256': E._sha(plan), 'targetRoots': list(ROOTS),
        'files': sorted(files, key=lambda row: row['path']), 'links': final_links,
        'ownedDirectories': [{'path': path, 'mode': mode, 'uid': 0, 'gid': 0} for path, mode in sorted(directories.items())],
        'registrationTargets': sorted(REGISTRATIONS.values()),
        'registrationParents': sorted({str(PurePosixPath(path).parent) for path in REGISTRATIONS.values()}),
        'existingRegistrationParentsMustNotBeChmodded': True,
        'roleManifestsInstalled': False, 'privatePackagingInstalled': False,
        **dict.fromkeys(('installable', 'readyForActivation', 'targetNamespaceVerified', 'registrationNamespaceVerified',
            'finalPermissionsApplied', 'runtimeLinksMaterialized', 'finalTargetsPublished', 'servicesRegistered',
            'servicesActivated', 'installationAuthorized', 'recoveryAuthorized'), False)}
    return E._encode(result, compact=True), MappingProxyType(readers)


def hold_final_install_layout(stack, user, source, recipes, raw):
    held = E.hold_owned_install_plan(stack, user, source, recipes, raw)
    original = held['read_plan']()
    plan, readers = _derive(original)

    def check():
        held['check']()
        if held['read_plan']() != original:
            raise ValueError('Original final placement authority changed')
        held['check']()

    def read_plan():
        check()
        result = plan
        check()
        return result

    def read_file(destination):
        if type(destination) is not str or destination not in readers:
            raise ValueError('Closed final placement file read required')
        check()
        kind, name, size, pin = readers[destination]
        data = held['read_source'](name) if kind == 'runtime' else held['read_recipe_file'](name)
        if (len(data), E._sha(data)) != (size, pin):
            raise ValueError('Final placement source differs from independent authority')
        check()
        return data

    check()
    return {'check': check, 'read_plan': read_plan, 'read_file': read_file, 'policy': {
        **held['policy'], 'kind': 'held-final-install-placement-plan', 'finalLayoutVerified': True,
        'finalLayoutSha256': E._sha(plan), 'plannedOwnedFiles': 250, 'plannedRegistrations': 9,
        'plannedLibraryAliases': 4, 'registrationNamespaceVerified': False, 'servicesRegistered': False,
        'finalPermissionsApplied': False, 'runtimeLinksMaterialized': False, 'finalTargetsPublished': False,
        'installationAuthorized': False, 'recoveryAuthorized': False}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5 or argv[0] != '--inspect-final-layout' or argv[4] != '-':
        raise ValueError('Only independent read-only final placement planning is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        result = hold_final_install_layout(stack, argv[1], argv[2], argv[3], raw)
        result['check']()
        print(json.dumps(result['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Final placement planning refused; no files or registrations changed', file=sys.stderr)
        sys.exit(1)
