# SPDX-License-Identifier: GPL-3.0-only
"""Pinned recipe-source packaging and read-only privileged owned runtime planning."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

_spec = importlib.util.spec_from_file_location('install_owned_profile', Path(__file__).with_name('runtime-owned-profile-plan.py'))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)
A = P.A
REVISION = 'b4e333fafc72f3479328123cfe50fc17012adda8'
PINS = MappingProxyType({
    'runtime/browser-connection/policy.js': (1825, 'e71f0bb2b3193716d40a9d52bd64334265d79c499e6c9531d9f06a79f9306cbb'),
    'runtime/portable/configuration-generation.js': (725, '971154e50a16d556c1488695b9a15467600ffdba55fdcb3e2bb29e53a59ff78b'),
    'runtime/portable/headless-owned-bundle.js': (10359, 'df7e45067f235a14af50223946f1afb4f240f27164749a01735f6a32f4bcf932'),
    'runtime/portable/headless-owned-runtime.py': (8501, 'e99b053dd76f1a8054bec920007887a1729f57810c96e18500d3399d807342d8'),
    'runtime/portable/headless-runtime.py': (12277, '2c0c34bf167411556e5a478669a86ec791a73e257cec117c596a0570af368d7b'),
    'runtime/portable/headless-session.py': (4701, '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba'),
    'runtime/portable/host-platform-compat.py': (4976, '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2'),
    'runtime/portable/owned-install-recipe.js': (5939, '2d6c397485e2d3f2d9d718562bb3c8e7cfc62bf06fff3b56b6e37da4fb322cba'),
    'runtime/portable/pid-bundle.js': (2663, 'fd06a5cc2930640d6f0d1d245e1cd7ce33346fae4501d223abb2d717f0652dc2'),
    'runtime/portable/pid-startup.js': (1165, '81576cbbaebed73c39d608cf7cba25a10b93bfc0020868d18c1ecf03acfc9d27'),
    'runtime/portable/read-bounded.js': (1265, '91eb96125edddb2f5f49b87732bf9c36f5342ff882b5f67a7c0a855e41071a56'),
    'runtime/portable/runtime-config.js': (3656, 'dc87ee44c58eecfb8fa73db775f99223be7180e24ba3d039fc81e1bba5ae79ce'),
    'runtime/portable/service-bundle.js': (4563, '13dffcd18c4a6ea88197dda01011e74db80562d722e8c2d5431d6c0cc8bd6284'),
    'runtime/portable/service-plan.js': (4842, 'abc5d2b465ce9d4c90b6ce9f9edf77772912ff6ef9b59eb56f22bba768cfcb7f'),
    'runtime/waydroid-display/pid-compat.js': (6162, '410d4b10294932f31cea99dcd8364e0a84bbecc5c84d1b4b952727f3f4e6d4ef'),
})
SERVICE_FILES = ('pam.d/droiddeck', 'systemd/system/droiddeck-browser-connection.service',
    'systemd/system/tesla-waydroid-compositor.service', 'systemd/system/tesla-waydroid-headless-session.service',
    'systemd/system/tesla-waydroid-runtime.service', 'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf',
    'tmpfiles.d/droiddeck.conf')
PID_FILES = ('portable/pid-startup.js', 'systemd/40-droiddeck-pid-compat.conf', 'waydroid-display/pid-compat.js')
HEADLESS_FILES = ('headless-owned-runtime.py', 'headless-runtime.py', 'headless-session.py',
    'host-platform-compat.py', 'policy.json', 'systemd/60-droiddeck-headless.conf')
RECIPE_FILES = tuple(sorted(('runtime.json', 'bundle-manifest.json',
    *(directory + '/' + name for directory, names in (('services', SERVICE_FILES), ('pid-compat', PID_FILES),
        ('headless-owned-runtime', HEADLESS_FILES)) for name in (*names, 'bundle-manifest.json')))))
FALSE_FLAGS = ('installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated')
SUMMARY_FALSE = (*FALSE_FLAGS, 'rootProvenanceVerified', 'profilePolicyVerified', 'filesWritten',
    'targetNamespaceVerified', 'gpuAccelerationVerified', 'codecOverlayVerified', 'independentRecoveryArmed')
SCRIPT = """const fs=require('node:fs');
const {createOwnedInstallRecipe}=require(process.argv[1]);
const raw=fs.readFileSync(0);if(raw.length>65536)throw Error('Bounded recipe request required');
const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
process.stdout.write(JSON.stringify(createOwnedInstallRecipe(Buffer.from(v.metadata,'utf8'),v.ownedPolicy)));"""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value, *, compact=False):
    return (json.dumps(value, ensure_ascii=True, sort_keys=compact,
        **({'separators': (',', ':')} if compact else {'indent': 2})) + '\n').encode('ascii')


def _records(files, names):
    return [{'path': name, 'size': len(files[name]), 'sha256': _sha(files[name])} for name in sorted(names)]


def source_manifest():
    return _encode({'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-recipe-source',
        'sourceRevision': REVISION, 'installable': False,
        'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(PINS.items())]}, compact=True)


def source_records(raw):
    if type(raw) is not bytes or raw != source_manifest():
        raise ValueError('Independent closed recipe-source manifest required')
    return PINS


def build_recipe_sources(repo, output):
    # Public source packaging only; privilege is deliberately unnecessary here.
    if sys.platform == 'linux' and os.geteuid() == 0:
        raise ValueError('Unprivileged source packaging required')
    repo, output = Path(repo), Path(output)
    if not repo.is_absolute() or not output.is_absolute() or str(output.resolve()) != str(output):
        raise ValueError('Canonical absolute source packaging paths required')
    files = {}
    environment = {'PATH': os.environ.get('PATH', ''), 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'}
    if 'SystemRoot' in os.environ:
        environment['SystemRoot'] = os.environ['SystemRoot']
    for name, (size, pin) in sorted(PINS.items()):
        child = subprocess.run(['git', '--no-pager', '--no-replace-objects', '--no-lazy-fetch', '-C', str(repo),
            'cat-file', 'blob', REVISION + ':' + name], capture_output=True, timeout=10, env=environment)
        if child.returncode or child.stderr or len(child.stdout) != size or _sha(child.stdout) != pin:
            raise ValueError('Reviewed Git source blob required')
        files[name] = child.stdout
    output.mkdir(mode=0o700)
    for name in sorted(files):
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open('xb') as stream:
            os.chmod(target, 0o600)
            stream.write(files[name])
            stream.flush()
            os.fsync(stream.fileno())
    with (output / A.MANIFEST).open('xb') as stream:
        os.chmod(output / A.MANIFEST, 0o600)
        stream.write(source_manifest())
        stream.flush()
        os.fsync(stream.fileno())
    return {'kind': 'owned-install-recipe-source', 'sourceFiles': 15, 'manifestSha256': _sha(source_manifest()),
        'sourceBundleCreated': True, 'installable': False, 'servicesActivated': False, 'androidOperationsExecuted': False}


def hold_recipe_sources(stack, root):
    return A._hold_pinned_tree(stack, root, owner=(0, 0), records_reader=source_records, manifest_limit=16384)


def _derive(root, raw, policy):
    request = json.dumps({'metadata': raw.decode('utf8'), 'ownedPolicy': json.loads(policy)}).encode('utf8')
    try:
        child = subprocess.run(['/usr/bin/node', '-e', SCRIPT, str(root / 'runtime/portable/owned-install-recipe.js')],
            input=request, capture_output=True, timeout=10, cwd='/', close_fds=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        if child.returncode or child.stderr or not 0 < len(child.stdout) <= 262144:
            raise ValueError('Recipe derivation refused')
        return json.loads(child.stdout)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ValueError('Recipe derivation refused') from None


def _admit_recipe(result, base, canonical, owned, sources):
    if type(result) is not dict or set(result) != {'files', 'summary'} or type(result['files']) is not dict or set(result['files']) != set(RECIPE_FILES):
        raise ValueError('Closed owned recipe result required')
    files = {}
    for name, text in result['files'].items():
        if type(text) is not str or '\r' in text:
            raise ValueError('Bounded UTF8 LF recipe required')
        raw = text.encode('utf8')
        if not 0 < len(raw) <= 16384:
            raise ValueError('Bounded recipe file required')
        files[name] = raw
    if files['runtime.json'] != canonical or files['headless-owned-runtime/policy.json'] != owned:
        raise ValueError('Recipe must match independently held settings and profiles')
    for name in ('headless-owned-runtime.py', 'headless-runtime.py', 'headless-session.py', 'host-platform-compat.py'):
        if files['headless-owned-runtime/' + name] != sources['read']('runtime/portable/' + name):
            raise ValueError('Headless recipe must retain independently held source bytes')
    for name, source in (('pid-compat/portable/pid-startup.js', 'runtime/portable/pid-startup.js'),
                         ('pid-compat/waydroid-display/pid-compat.js', 'runtime/waydroid-display/pid-compat.js')):
        if files[name] != sources['read'](source):
            raise ValueError('PID recipe must retain independently held source bytes')
    value, policy = json.loads(base), json.loads(owned)
    generation = value['configurationDigest']
    for directory, names in (('services', SERVICE_FILES), ('pid-compat', PID_FILES), ('headless-owned-runtime', HEADLESS_FILES)):
        child = {name: files[directory + '/' + name] for name in names}
        manifest = {'version': 1, 'product': 'DroidDeck', 'kind': {
            'services': 'service-component', 'pid-compat': 'pid-startup-component',
            'headless-owned-runtime': 'headless-owned-session-component'}[directory]}
        if directory == 'services':
            manifest.update({'installable': False, 'sourcePlanSha256': PINS['runtime/portable/service-plan.js'][1], 'configurationDigest': generation})
        elif directory == 'pid-compat':
            manifest.update({'installable': False, 'configurationDigest': generation})
        else:
            manifest.update({'configurationDigest': generation, 'originalConfigurationSha256': policy['originalConfigurationSha256'],
                'profiles': policy['profiles'], **dict.fromkeys(FALSE_FLAGS, False)})
        manifest['files'] = _records(child, names)
        if files[directory + '/bundle-manifest.json'] != _encode(manifest):
            raise ValueError('Child recipe generation differs')
    manifest = {'version': 1, 'product': 'DroidDeck', 'kind': 'owned-gpu-install-recipe',
        'configurationDigest': generation, 'sourceMetadataSha256': value['sourceMetadataSha256'],
        'originalConfigurationSha256': policy['originalConfigurationSha256'], 'profiles': policy['profiles'],
        **dict.fromkeys(FALSE_FLAGS, False), 'files': _records(files, tuple(name for name in RECIPE_FILES if name != A.MANIFEST))}
    if files[A.MANIFEST] != _encode(manifest):
        raise ValueError('Outer recipe generation differs')
    summary = {'kind': 'owned-gpu-install-recipe', 'payloadFiles': 20, 'manifestFiles': 4,
        'configurationDigest': generation, 'sourceMetadataSha256': value['sourceMetadataSha256'],
        'canonicalMetadataSha256': _sha(canonical), 'serviceManifestSha256': _sha(files['services/bundle-manifest.json']),
        'pidManifestSha256': _sha(files['pid-compat/bundle-manifest.json']),
        'ownedHeadlessManifestSha256': _sha(files['headless-owned-runtime/bundle-manifest.json']),
        'ownedProfilePolicySha256': _sha(owned), 'profileManifestSha256': _sha(files[A.MANIFEST]),
        'recipesDerived': True, 'sourceRecipeCodeExecuted': True, **dict.fromkeys(SUMMARY_FALSE, False)}
    if type(result['summary']) is not dict or _encode(result['summary'], compact=True) != _encode(summary, compact=True):
        raise ValueError('Recipe summary generation differs')
    return files


def hold_owned_install_plan(stack, user, source, recipe_root, raw):
    A._root_linux()
    P.C._bounded(raw)
    source, recipe_root = A._canonical(source), A._canonical(recipe_root)
    target = A._canonical('/opt/droiddeck')
    for other in (source, target):
        if recipe_root == other or recipe_root.is_relative_to(other) or other.is_relative_to(recipe_root):
            raise ValueError('Disjoint recipe-source namespace required')
    recipes = hold_recipe_sources(stack, recipe_root)
    held = P.hold_owned_profile_runtime_plan(stack, user, source, raw)

    def check():
        recipes['check']()
        held['check']()
        recipes['check']()

    check()
    canonical, owned, base = held['read_configuration'](), held['read_owned_policy'](), held['read_plan']()
    result = _derive(recipe_root, raw, owned)
    check()
    files = _admit_recipe(result, base, canonical, owned, recipes)
    check()
    value = json.loads(base)
    digests = {'profileBoundPlanSha256': _sha(base), 'recipeSourceManifestSha256': _sha(source_manifest()),
        'ownedRecipeManifestSha256': _sha(files[A.MANIFEST])}
    value.update({'kind': 'owned-install-runtime-artifact-placement-plan', 'ownedRecipeFiles': _records(files, RECIPE_FILES), **digests})
    plan = _encode(value, compact=True)

    def checked(read, *args):
        check()
        result = read(*args)
        check()
        return result

    def read_recipe_file(name):
        if type(name) is not str or name not in files:
            raise ValueError('Closed recipe file read required')
        return checked(lambda: files[name])

    check()
    return {'check': check, 'read_plan': lambda: checked(lambda: plan), 'read_recipe_file': read_recipe_file,
        'read_configuration': lambda: checked(held['read_configuration']), 'read_owned_policy': lambda: checked(held['read_owned_policy']),
        'read_source': lambda name: checked(held['read_source'], name), 'policy': {
            **held['policy'], 'kind': 'held-owned-install-runtime-plan', 'recipeSourceFilesVerified': 16,
            'recipeSourceProvenanceVerified': True, 'ownedRecipeVerified': True, 'ownedRecipeFilesVerified': 21,
            'sourceRecipeCodeExecuted': True, 'recipeFilesWritten': False, **digests, 'planSha256': _sha(plan)}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == '--build-recipe-sources':
        print(json.dumps(build_recipe_sources(argv[1], argv[2]), sort_keys=True, indent=2))
    elif len(argv) == 5 and argv[0] == '--inspect-owned-install-plan' and argv[4] == '-':
        raw = sys.stdin.buffer.read(P.C.LIMIT + 1)
        with ExitStack() as stack:
            held = hold_owned_install_plan(stack, argv[1], argv[2], argv[3], raw)
            held['read_plan']()
            print(json.dumps(held['policy'], sort_keys=True, indent=2))
    else:
        raise ValueError('Only pinned source packaging or fixed read-only planning is exposed')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, P.configparser.Error):
        print('Owned install planning refused; no deployment performed', file=sys.stderr)
        sys.exit(1)
