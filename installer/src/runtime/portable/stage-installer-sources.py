# SPDX-License-Identifier: GPL-3.0-only
"""Publish protected recipe/compat/GPU sources from a verified source package."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

spec = importlib.util.spec_from_file_location('source_stage_host', Path(__file__).with_name('host-prerequisites.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
ROOT = Path('/root/droiddeck-installer-sources')
GPU_ROOT = Path('/var/lib/droiddeck/gpu-recovery')
MODES = ('--stage-installer-sources', '--resume-installer-sources', '--inspect-installer-sources')
GPU_MODES = ('--stage-gpu-sources', '--resume-gpu-sources', '--inspect-gpu-sources')
TOOLS = ('tools/waydroid-layer-property.py', 'tools/waydroid-gpu-health-policy.py')
RECIPES = 'build/installation-inputs/recipes/'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def literal(raw, name, wrapped=False):
    nodes = [node.value for node in ast.parse(raw).body if isinstance(node, ast.Assign)
             and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name]
    if len(nodes) != 1:
        raise ValueError('Single literal independent source pin required')
    value = nodes[0]
    if wrapped:
        if (not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name)
                or value.func.id != 'MappingProxyType' or len(value.args) != 1 or value.keywords):
            raise ValueError('Closed recipe pins required')
        value = value.args[0]
    return ast.literal_eval(value)


def read_package(package):
    package = Path(package)
    raw = H.protected(package / 'package-manifest.json', maximum=1024 * 1024)
    value = json.loads(raw)
    if (value.get('kind') != 'source-release-candidate' or value.get('product') != 'DroidDeck'
            or not re.fullmatch('[0-9a-f]{40}', value.get('sourceRevision', ''))
            or value.get('installable') is not False or not isinstance(value.get('files'), list)):
        raise ValueError('Verified source candidate manifest required')
    files = {}
    for record in value['files']:
        name = record['path']
        path = PurePosixPath(name)
        if (not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}', name) or str(path) != name
                or path.is_absolute() or any(part in ('.', '..') for part in path.parts)
                or name in files or type(record['size']) is not int or not 0 <= record['size'] <= 1048576):
            raise ValueError('Closed bounded source records required')
        # Read EVERY manifest member, not just the sources being copied. The
        # archive must first be independently verified before root extraction.
        content = H.protected(package / name, maximum=1048576, allow_empty=True)
        if len(content) != record['size'] or digest(content) != record['sha256']:
            raise ValueError('Original complete source package required')
        files[name] = content
    if not 1 <= len(files) <= 1100 or sum(map(len, files.values())) > 16 * 1024 * 1024:
        raise ValueError('Bounded complete source package required')
    sources = {name[4:]: content for name, content in files.items() if name.startswith('src/')}
    plan = sources['runtime/portable/runtime-owned-install-plan.py']
    pins = literal(plan, 'PINS', wrapped=True)
    revision = literal(plan, 'REVISION')
    expected_manifest = H.encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-recipe-source',
        'sourceRevision': revision, 'installable': False,
        'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(pins.items())]})
    recipes = {name[len(RECIPES):]: content for name, content in sources.items() if name.startswith(RECIPES)}
    if len(pins) != 15 or set(recipes) != {*pins, 'bundle-manifest.json'} or recipes['bundle-manifest.json'] != expected_manifest:
        raise ValueError('Complete independently pinned fifteen-source recipe required')
    for name, (size, pin) in pins.items():
        if len(recipes[name]) != size or digest(recipes[name]) != pin:
            raise ValueError('Independent recipe source pin mismatch')
    compat = sources['runtime/waydroid-display/location-adapter.js']
    if digest(compat) != literal(sources['runtime/portable/runtime-compat-install.py'], 'PIN'):
        raise ValueError('Independent compatibility source pin mismatch')
    for name, pin in literal(sources['runtime/portable/gpu-activation.py'], 'PINS').items():
        if digest(sources[name]) != pin:
            raise ValueError('Independent GPU source pin mismatch')
    output = {name: content for name, content in sources.items()
              if name.startswith('runtime/portable/') or name in TOOLS}
    output.update({'recipes/' + name: content for name, content in recipes.items()})
    output['compat-source.js'] = compat
    return output, digest(raw)


def directories(files):
    return {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if parent != PurePosixPath('.')}


def finish(fd, intent, files, mode, *, after_file=None, root=None):
    root = ROOT if root is None else root
    if mode not in (*MODES, *GPU_MODES):
        raise ValueError('Exact source publication operation required')
    if H.protected(root / 'intent.json', maximum=1048576) != H.encode(intent):
        raise ValueError('Original source publication intent required')
    if [os.fstat(fd).st_dev, os.fstat(fd).st_ino] != intent['rootIdentity']:
        raise ValueError('Original source output directory required')
    allowed_dirs = directories(files)
    allowed_files = {*files, 'intent.json', 'complete.json'}
    # No unknown partial files, links or directories are silently adopted.
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            if (relative not in (allowed_dirs if directory else allowed_files)
                    or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                    or not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                raise ValueError('Only original private publication objects allowed')
    complete = os.path.lexists(root / 'complete.json')
    if complete and H.protected(root / 'complete.json', maximum=1048576) != H.encode(intent):
        raise ValueError('Original completion record required')
    readonly = complete or mode in ('--inspect-installer-sources', '--inspect-gpu-sources')
    if not complete and readonly:
        raise ValueError('Source publication incomplete')
    for name in sorted(allowed_dirs, key=lambda name: (len(PurePosixPath(name).parts), name)):
        target = root / name
        if not os.path.lexists(target):
            if readonly:
                raise ValueError('Original source directory missing')
            target.mkdir(mode=0o700)
            parent = H.protected(target.parent, directory=True)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    for name, raw in sorted(files.items()):
        target = root / name
        if os.path.lexists(target):
            if H.protected(target, maximum=1048576, allow_empty=True) != raw:
                raise ValueError('Partial or foreign source preserved, never overwritten')
        elif readonly:
            raise ValueError('Original source file missing')
        else:
            parent = H.protected(target.parent, directory=True)
            try:
                H.write_new(parent, target.name, raw)
            finally:
                os.close(parent)
        if after_file and not readonly:
            after_file(name)
    if not complete:
        H.write_new(fd, 'complete.json', H.encode(intent))
    return {'kind': 'protected-installer-sources', 'sourceFiles': len(files),
            'recipes': str(root / 'recipes'), 'compatibilitySource': str(root / 'compat-source.js'),
            'gpuSources': str(root), 'readOnly': readonly, 'servicesActivated': False,
            'wholeInstallationVerified': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 1 or args[0] not in (*MODES, *GPU_MODES) or sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Exact root Linux installer-source operation required')
    package = Path(__file__).absolute().parents[3]
    files, pin = read_package(package)
    root = GPU_ROOT if args[0] in GPU_MODES else ROOT
    os.umask(0o077)
    initial = args[0] in ('--stage-installer-sources', '--stage-gpu-sources')
    if initial:
        root.mkdir(mode=0o700)
    fd = H.protected(root, directory=True)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError('Original private source output required')
        H.fcntl.flock(fd, H.fcntl.LOCK_EX | H.fcntl.LOCK_NB)
        intent = {'schema': 1, 'packageManifestSha256': pin,
                  'root': str(root),
                  'machineSha256': digest(H.protected('/etc/machine-id')),
                  'rootIdentity': [os.fstat(fd).st_dev, os.fstat(fd).st_ino],
                  'files': [{'path': name, 'size': len(raw), 'sha256': digest(raw)} for name, raw in sorted(files.items())]}
        if initial:
            H.write_new(fd, 'intent.json', H.encode(intent))
        print(json.dumps(finish(fd, intent, files, args[0], root=root), sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Source publication incomplete; preserve original sources and journal: ' + str(error), file=sys.stderr)
        sys.exit(1)
