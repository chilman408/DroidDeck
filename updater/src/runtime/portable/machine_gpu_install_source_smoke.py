# SPDX-License-Identifier: GPL-3.0-only
"""Actual source-only installer in a private generated chroot; no service actions."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('install_smoke_package', HERE / 'machine-gpu-package.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
TARGETS = tuple(map(Path, ('/opt', '/opt/droiddeck', '/opt/droiddeck/machine-gpu-entry.py',
    '/var/lib/droiddeck', str(P.STAGING_PARENT), str(P.COPY_STAGING_PARENT))))


def require(value, message):
    if not value:
        raise ValueError(message)


def identity(path):
    try:
        return P._identity(path.lstat())
    except FileNotFoundError:
        return None


def snapshot(context, root):
    context['check']()
    result = {name: (identity(root / name), context['read'](name)) for name in context['files']}
    context['check']()
    return result


def child(fixture, source, user, parent_namespace, boundary):
    P._root_linux()
    require(os.readlink('/proc/' + str(os.getppid()) + '/ns/mnt') == parent_namespace
        and os.readlink('/proc/self/ns/mnt') != parent_namespace, 'Independent private child namespace required')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        require(not any(v.startswith(('shared:', 'master:')) for v in fields[6:fields.index('-')]), 'Private propagation required')
    root = Path(fixture)
    require(str(root) == os.path.abspath(root) and len(root.parts) == 4 and root.parts[1] == 'root'
        and root.parent.name.startswith('droiddeck-install-cli-') and root.name == 'rootfs', 'Generated rootfs required')
    info = root.parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
        'Private root fixture ancestor required')
    for parent, directories, files in os.walk(root, followlinks=False):
        require(not files, 'Empty generated rootfs directory inventory required')
        for path in [Path(parent), *(Path(parent) / name for name in directories)]:
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
                'Private generated directories required')

    def bind(original, target, *, readonly=True):
        subprocess.run(['/usr/bin/mount', '--bind', str(original), str(target)], check=True, capture_output=True, timeout=10)
        if readonly:
            subprocess.run(['/usr/bin/mount', '-o', 'remount,bind,ro', str(target)], check=True, capture_output=True, timeout=10)
            require(os.statvfs(target).f_flag & os.ST_RDONLY, 'Read-only dependency bind required')

    # Only public runtime/NSS files and this explicit source package are visible.
    bind('/usr', root / 'usr')
    for name in ('bin', 'sbin', 'lib', 'lib64'):
        original = Path('/') / name
        if original.is_symlink():
            link = os.readlink(original)
            require(link == 'usr/' + name, 'Expected runtime library alias required')
            (root / name).symlink_to(link)
        elif original.is_dir():
            (root / name).mkdir(mode=0o700)
            bind(original, root / name)
    passwd = root / 'etc/passwd'
    passwd.touch(mode=0o600)
    bind('/etc/passwd', passwd)
    bind('/proc', root / 'proc')
    bind(HERE.parents[1], root / 'test-source')
    bind(source, root / source.lstrip('/'))
    if boundary == 'separate':
        bind(root / 'opt-other', root / 'opt', readonly=False)
    elif boundary in ('copy-cross-device', 'user-copy-cross-device'):
        subprocess.run(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'size=16m,mode=0700,nosuid,nodev,noexec',
            'tmpfs', str(root / 'opt')], check=True, capture_output=True, timeout=10)
        if boundary == 'copy-cross-device':
            copy_base = root / 'opt/droiddeck-machine-gpu-source-staging'
            copy_base.mkdir(mode=0o700)
            (copy_base / 'candidate').mkdir(mode=0o700)
    elif boundary != 'shared':
        raise ValueError('Closed generated mount boundary mode required')
    os.chroot(root)
    os.chdir('/')
    env = {'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'}
    file = '/test-source/runtime/portable/machine-gpu-package.py'
    def run(*args):
        return subprocess.run(['/usr/bin/python3', '-I', '-B', file, *args], env=env,
            capture_output=True, text=True, timeout=30)
    direct = boundary == 'user-copy-cross-device'
    root_slot = Path(P.STAGING_PARENT) / 'candidate'
    before_root_slot = identity(root_slot)
    if direct:
        require(not Path(P.COPY_STAGING_PARENT).exists() and not os.listdir(root_slot), 'Unprepared destination/unused root source slot required')
    else:
        staged = run('--stage-user-package', user, source, 'candidate')
        require(staged.returncode == 0, 'Actual isolated staging command must succeed')
        staged_policy = json.loads(staged.stdout)
        require(staged_policy.get('stagingOnly') is True and all(staged_policy.get(key) is False for key in P.FALSE_FLAGS),
            'Staging is not activation authority')
    package = Path(source) if direct else root_slot / 'package'
    owner = P._source_account(user)[1:3] if direct else (0, 0)
    copying = boundary in ('copy-cross-device', 'user-copy-cross-device')
    mode = '--copy-staged-sources' if copying else '--install-staged-sources'
    command = ('--install-user-sources', user, source, 'candidate') if direct else (mode, 'candidate')
    if copying:
        require(package.stat().st_dev != Path('/opt').stat().st_dev, 'Actual different-device installation fixture required')
        with ExitStack() as mounts:
            source_fd, _ = P._install_parent(mounts, package.parent, private=True)
            target_fd, _ = P._install_parent(mounts, Path('/opt'), private=False)
            require(P._mount_id(source_fd) != P._mount_id(target_fd), 'Actual different mount IDs required')
    with ExitStack() as stack:
        context = P.hold_source_package(stack, package, owner=owner)
        before = snapshot(context, package)
        generations = context['generations']
        installed = run(*command)
        if boundary == 'separate':
            require(installed.returncode == 1 and installed.stdout == '' and before == snapshot(context, package)
                and not Path('/opt/droiddeck').exists(), 'Separate bind mount must refuse before moving sources')
            print(json.dumps({'separateMountRefused': True, 'stagingUnchanged': True, 'servicesActivated': False}))
            return
        if copying:
            require(before == snapshot(context, package), 'Cross-device copy must retain original staged source')
    require(installed.returncode == 0, 'Actual isolated new-directory source installation must succeed')
    policy = json.loads(installed.stdout)
    require(policy.get('sourceFilesInstalled') is True and policy.get('newDirectoryOnly') is True
        and all(policy.get(key) is False for key in P.FALSE_FLAGS), 'Source installation is not activation authority')
    if copying:
        retained = 'sourceInputRetained' if direct else 'stagedSourceRetained'
        method = 'user-copy-then-publish' if direct else 'copy-then-publish'
        require(policy.get(retained) is True and policy.get('installationMethod') == method,
            'Explicit copy installation policy required')
        if direct:
            require(policy.get('sourceAccountMatched') is True, 'Actual NSS source-account admission required')
            for path in (Path(P.COPY_STAGING_PARENT), Path(P.COPY_STAGING_PARENT) / 'candidate'):
                info = path.lstat()
                require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700), 'Automatically prepared private source directories required')
    target = Path('/opt/droiddeck')
    with ExitStack() as stack:
        context = P.hold_source_package(stack, target)
        require((context['generations'] != generations) if copying else (context['generations'] == generations),
            'Expected copied/original source generation relationship required')
        installed_before = snapshot(context, target)
        if copying:
            require({name: record[1] for name, record in installed_before.items()} == {name: record[1] for name, record in before.items()},
                'Independent installed copy must match every original source byte')
        repeat = run(*command)
        require(repeat.returncode == 1 and repeat.stdout == '', 'Duplicate installation must refuse')
        launcher = subprocess.run(['/usr/bin/python3', '-I', '-B', str(target / 'machine-gpu-entry.py'), '--verify-sources'],
            env=env, capture_output=True, text=True, timeout=30)
        require(launcher.returncode == 0, 'Actual default launcher dependency verification must succeed')
        proof = json.loads(launcher.stdout)
        require(proof.get('verifiedSourceCount') == 10 and proof.get('bootstrapPathMatched') is True
            and proof.get('installedRootMatched') is True
            and all(proof.get(key) is False for key in P.FALSE_FLAGS), 'Closed inert default launcher proof required')
        require(installed_before == snapshot(context, target), 'Installed source generations and bytes must remain unchanged')
    if copying:
        with ExitStack() as stack:
            require(before == snapshot(P.hold_source_package(stack, package, owner=owner), package), 'Copy/launcher/repeat must preserve source input')
        require(not os.listdir(Path(P.COPY_STAGING_PARENT) / 'candidate'), 'Successful copy installation leaves prepared copy slot empty')
        if direct:
            require(identity(root_slot) == before_root_slot and not os.listdir(root_slot), 'Direct user source installation must not use root source staging')
            print(json.dumps({'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
                'independentCopy': True, 'userSourceUnchanged': True, 'rootSourceStagingUnused': True,
                'automaticPrivatePreparation': True, 'differentDevices': True, 'differentMounts': True,
                'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}))
            return
        print(json.dumps({'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
            'independentCopy': True, 'sourceStagingUnchanged': True, 'differentDevices': True, 'differentMounts': True,
            'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}))
        return
    require(not package.exists() and not os.listdir(package.parent), 'Successful install consumes staged package only')
    print(json.dumps({'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
        'originalGenerationsInstalled': True, 'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}))


def main(source, user, *, copying=False, direct=False):
    P._root_linux()
    source = str(P._canonical(source))
    account = P._source_account(user)
    require(Path(source).is_relative_to(account[3]) and source != account[3], 'Actual user source beneath home required')
    namespace = os.readlink('/proc/self/ns/mnt')
    host = tuple(identity(path) for path in TARGETS)
    proofs = []
    with ExitStack() as stack:
        original = P.hold_source_package(stack, source, owner=account[1:3])
        before = snapshot(original, Path(source))
        for boundary in (('user-copy-cross-device',) if direct else ('copy-cross-device',) if copying else ('separate', 'shared')):
            with tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-install-cli-') as temporary:
                root = Path(temporary) / 'rootfs'
                for relative in ('usr', 'etc', 'proc', 'test-source', 'opt', 'opt-other',
                                 'var/lib/droiddeck/machine-gpu-source-staging/candidate', source.lstrip('/')):
                    (root / relative).mkdir(mode=0o700, parents=True)
                for parent, _, _ in os.walk(root):
                    Path(parent).chmod(0o700)
                result = subprocess.run(['/usr/bin/unshare', '--mount', '--propagation', 'private',
                    '/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), '--child', str(root), source,
                    user, namespace, boundary], capture_output=True, text=True, timeout=90)
                require(result.returncode == 0, 'Generated isolated source installer proof failed: ' + result.stderr.strip())
                proofs.append(json.loads(result.stdout))
            require(namespace == os.readlink('/proc/self/ns/mnt') and host == tuple(identity(path) for path in TARGETS),
                'Host namespace and installation/staging targets must remain unchanged')
            require(before == snapshot(original, Path(source)), 'Original ordinary-user source must remain unchanged')
    expected = ([{'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
        'independentCopy': True, 'userSourceUnchanged': True, 'rootSourceStagingUnused': True,
        'automaticPrivatePreparation': True, 'differentDevices': True, 'differentMounts': True,
        'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}] if direct else
        [{'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
        'independentCopy': True, 'sourceStagingUnchanged': True, 'differentDevices': True, 'differentMounts': True,
        'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}] if copying else
        [{'separateMountRefused': True, 'stagingUnchanged': True, 'servicesActivated': False},
        {'defaultInstallPathVerified': True, 'installedSourceFiles': 12, 'verifiedDependencySources': 10,
         'originalGenerationsInstalled': True, 'repeatRefused': True, 'servicesActivated': False, 'readyForActivation': False}])
    require(proofs == expected, 'Exact generated mount proofs required')
    print(json.dumps({'privateMountNamespace': True, 'privateGeneratedChroot': True, 'hostUnchanged': True,
        'inputSourceUnchanged': True, 'proofs': proofs}, sort_keys=True))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 7 and sys.argv[1] == '--child':
            child(*sys.argv[2:])
        elif len(sys.argv) == 4 and sys.argv[1] == '--copy-proof':
            main(*sys.argv[2:], copying=True)
        elif len(sys.argv) == 4 and sys.argv[1] == '--user-proof':
            main(*sys.argv[2:], direct=True)
        elif len(sys.argv) == 3:
            main(*sys.argv[1:])
        else:
            raise ValueError('Explicit generated source and runtime user required')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = ': ' + str(error) if isinstance(error, ValueError) else ''
        print('Private generated source installation proof refused; no service activation' + detail, file=sys.stderr)
        sys.exit(1)
