# SPDX-License-Identifier: GPL-3.0-only
"""Real staging CLI in a proven private mount namespace; generated data only."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

FILE = Path(__file__).with_name('machine-gpu-package.py')
TARGET = Path('/var/lib/droiddeck')
spec = importlib.util.spec_from_file_location('stage_namespace_package', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def require(value, message):
    if not value:
        raise ValueError(message)


def snapshot(context, root):
    context['check']()
    result = {name: (P._identity((root / name).lstat()), context['read'](name)) for name in context['files']}
    context['check']()
    return result


def named_identity(path):
    try:
        return P._identity(path.lstat())
    except FileNotFoundError:
        return None


def child(directory, source, user, namespace):
    P._root_linux()
    parent = os.readlink('/proc/' + str(os.getppid()) + '/ns/mnt')
    require(parent == namespace and os.readlink('/proc/self/ns/mnt') != parent, 'Independent child namespace required')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        require(not any(value.startswith(('shared:', 'master:')) for value in fields[6:fields.index('-')]), 'Private propagation required')
    root = Path(directory)
    require(str(root) == os.path.abspath(root) and len(root.parts) == 4 and root.parts[1] == 'root'
        and root.parent.name.startswith('droiddeck-stage-cli-') and root.name == 'data-root', 'Generated fixture required')
    expected = {'': {'machine-gpu-source-staging'}, 'machine-gpu-source-staging': {'candidate', 'fault'},
        'machine-gpu-source-staging/candidate': set(), 'machine-gpu-source-staging/fault': set()}
    for relative, names in expected.items():
        path = root / relative
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700)
            and set(os.listdir(path)) == names, 'Closed root-private fixture required')
    for ancestor in (Path('/'), Path('/var'), Path('/var/lib'), TARGET):
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'Protected real target ancestor required')
    subprocess.run(['/usr/bin/mount', '--bind', str(root), str(TARGET)], check=True, capture_output=True, timeout=10)
    env = {'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'}
    def run(slot, *, fault=False, mode='--stage-user-package'):
        command = ['/usr/bin/python3', '-I', '-B']
        if fault:
            command += ['-c', "import os,runpy,sys\ndef fail(_): raise OSError('generated flush failure')\nos.fsync=fail\nsys.argv=sys.argv[1:]\nrunpy.run_path(sys.argv[0],run_name='__main__')"]
        command += [str(FILE), mode, user, source, slot]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    result = run('candidate')
    require(result.returncode == 0, 'Real source staging command failed')
    policy = json.loads(result.stdout)
    require(all(policy.get(key) is True for key in ('stagingOnly', 'sourceAccountMatched', 'rootPrivateSourcePackage',
        'unprivilegedSourceAdmitted', 'newSourcePackagePublished')) and policy.get('payloadSourcesVerified') == 11
        and all(policy.get(key) is False for key in P.FALSE_FLAGS), 'Closed source-only policy required')
    output = Path(P.STAGING_PARENT) / 'candidate/package'
    with ExitStack() as stack:
        context = P.hold_source_package(stack, output)
        before = snapshot(context, output)
        repeat = run('candidate')
        unsupported = run('candidate', mode='--install')
        require(repeat.returncode == unsupported.returncode == 1 and repeat.stdout == unsupported.stdout == '', 'Refusing repeat/install required')
        require(before == snapshot(context, output), 'Published source must remain unchanged')
    uncertain = run('fault', fault=True)
    require(uncertain.returncode == 3 and uncertain.stdout == '' and 'preserve slot' in uncertain.stderr
        and source not in uncertain.stderr, 'Sanitized uncertainty exit required')
    fault_slot = Path(P.STAGING_PARENT) / 'fault'
    attempts = list(fault_slot.iterdir())
    require(len(attempts) == 1 and attempts[0].name.startswith('.source-attempt-')
        and not (fault_slot / 'package').exists() and not any(fault_slot.rglob(P.MANIFEST)), 'Partial attempt must be preserved')
    retry = run('fault')
    require(retry.returncode == 1 and retry.stdout == '' and list(fault_slot.iterdir()) == attempts, 'Uncertain slot retry must refuse')
    print(json.dumps({'privateMountNamespace': True, 'defaultStagingPathVerified': True,
        'successfulStageFiles': len(before), 'repeatRefused': True, 'uncertainExitCode': 3,
        'partialPreserved': True, 'servicesActivated': False, 'readyForActivation': False}, sort_keys=True))


def main(args):
    P._root_linux()
    require(len(args) == 2, 'Explicit generated source and user required')
    source, user = Path(args[0]), args[1]
    account = P._source_account(user)
    namespace = os.readlink('/proc/self/ns/mnt')
    before_target = named_identity(TARGET)
    base = Path(P.STAGING_PARENT)
    before_base = named_identity(base)
    with ExitStack() as stack:
        original = P.hold_source_package(stack, source, owner=account[1:3])
        before_source = snapshot(original, source)
        with tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-stage-cli-') as temporary:
            fixture = Path(temporary) / 'data-root'
            fixture.mkdir(mode=0o700)
            staging = fixture / 'machine-gpu-source-staging'
            staging.mkdir(mode=0o700)
            for name in ('candidate', 'fault'):
                (staging / name).mkdir(mode=0o700)
            result = subprocess.run(['/usr/bin/unshare', '--mount', '--propagation', 'private',
                '/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), '--child', str(fixture), str(source), user, namespace],
                capture_output=True, text=True, timeout=90)
            require(result.returncode == 0, 'Private namespace staging CLI proof failed')
            proof = json.loads(result.stdout)
            require(proof == {'privateMountNamespace': True, 'defaultStagingPathVerified': True,
                'successfulStageFiles': 12, 'repeatRefused': True, 'uncertainExitCode': 3,
                'partialPreserved': True, 'servicesActivated': False, 'readyForActivation': False}, 'Exact generated CLI proof required')
        require(os.readlink('/proc/self/ns/mnt') == namespace and named_identity(TARGET) == before_target
            and named_identity(base) == before_base, 'Host namespace/target/staging must remain unchanged')
        require(before_source == snapshot(original, source), 'Original user source must remain unchanged')
    print(json.dumps({**proof, 'hostNamespaceUnchanged': True, 'inputSourceUnchanged': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 6 and sys.argv[1] == '--child':
            child(*sys.argv[2:])
        else:
            main(sys.argv[1:])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print('Private generated staging CLI proof refused; no service activation', file=sys.stderr)
        sys.exit(1)
