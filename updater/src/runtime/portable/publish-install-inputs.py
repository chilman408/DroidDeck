# SPDX-License-Identifier: GPL-3.0-only
"""Compose independently pinned assembly publication, never installed targets."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('runtime-artifact-assembly')
R = load('runtime-registration-transaction')
W, S, A = C.W, C.S, C.A
MODES = ('--publish-install-inputs', '--verify-install-inputs')


def identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]


def finish(user, prepared, output, *, verify=False):
    A._root_linux()
    source, root = Path(A._canonical(prepared)), Path(A._canonical(output))
    account = A._account(user)
    if (not source.is_relative_to(account[3]) or not root.is_relative_to('/root') or root == Path('/root')
            or source.is_relative_to(root) or root.is_relative_to(source)):
        raise ValueError('Private user preparation and new root-private publication namespace required')
    with ExitStack() as stack:
        dirs = R._Directories(stack)
        parent = dirs.open(root.parent)
        if not verify:
            os.mkdir(root.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        fd = dirs.open(root)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError('Original root-private publication namespace required')
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = {name: root / name for name in ('candidate-stage', 'dependency-stage', 'artifact-stage')}
        if not verify:
            S._write_private(stack, fd, 'intent.json', R._encode({
                'version': 1, 'kind': 'installation-input-publication', 'user': user,
                'prepared': str(source), 'publicationIdentity': identity(os.fstat(fd))}))
            for path in paths.values():
                os.mkdir(path.name, 0o700, dir_fd=fd)
            os.fsync(fd)
            records = {}
            # Each next publisher independently admits its protected predecessor;
            # do not accumulate hundreds of no-follow descriptors across phases.
            with ExitStack() as phase:
                candidate = W.S.publish_user_candidate(phase, user, source / 'runtime', paths['candidate-stage'])
                candidate['check']()
                records['candidate'] = identity((paths['candidate-stage'] / 'candidate').stat())
            with ExitStack() as phase:
                runtime = W.publish_runtime_assembly(phase, user, paths['candidate-stage'] / 'candidate',
                    source / 'gateway-dependencies', source / 'display-dependencies', paths['dependency-stage'])
                runtime['check']()
                records['runtime'] = identity((paths['dependency-stage'] / 'assembly').stat())
            with ExitStack() as phase:
                combined = C.publish_runtime_artifact_assembly(phase, user, paths['dependency-stage'] / 'assembly',
                    source / 'artifact-pair/first/artifacts', paths['artifact-stage'])
                combined['check']()
                records['artifacts'] = identity((paths['artifact-stage'] / 'assembly').stat())
            S._write_private(stack, fd, 'complete.json', R._encode({'version': 1,
                'kind': 'installation-input-publication', 'user': user, 'prepared': str(source),
                'publicationIdentity': identity(os.fstat(fd)), 'objects': records}))
            os.fsync(fd)
        raw = R.A._read(os_open(stack, fd, 'complete.json'), 16384)
        receipt = json.loads(raw)
        intent = R.A._read(os_open(stack, fd, 'intent.json'), 16384)
        expected = {'version', 'kind', 'user', 'prepared', 'publicationIdentity', 'objects'}
        if (set(receipt) != expected or receipt['version'] != 1 or receipt['kind'] != 'installation-input-publication'
                or receipt['user'] != user or receipt['prepared'] != str(source)
                or receipt['publicationIdentity'] != identity(os.fstat(fd))
                or set(receipt['objects']) != {'candidate', 'runtime', 'artifacts'} or R._encode(receipt) != raw
                or intent != R._encode({key: receipt[key] for key in expected - {'objects'}})
                or set(os.listdir(fd)) != {'intent.json', 'complete.json', *paths}):
            raise ValueError('Original completed installation publication required')
        for name, path, verifier in (
            ('candidate', paths['candidate-stage'] / 'candidate', A.hold_runtime_candidate),
            ('runtime', paths['dependency-stage'] / 'assembly', W.hold_runtime_assembly),
            ('artifacts', paths['artifact-stage'] / 'assembly', C.hold_runtime_artifact_assembly)):
            if receipt['objects'][name] != identity(path.stat()):
                raise ValueError('Original completed assembly inode required')
            with ExitStack() as phase:
                verifier(phase, path)['check']()
        dirs.check()
        return {'kind': 'protected-installation-inputs', 'assembly': str(paths['artifact-stage'] / 'assembly'),
                'payloadFilesVerified': 235, 'readOnly': verify, 'installable': False,
                'servicesActivated': False, 'androidOperationsExecuted': False}


def os_open(stack, directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink,
            stat.S_IMODE(info.st_mode)) != (0, 0, 1, 0o600) or not 0 < info.st_size <= 16384):
        raise ValueError('Original protected publication receipt required')
    return fd


def main(args):
    if len(args) != 4 or args[0] not in MODES:
        raise ValueError('Publication operation USER PREPARED_INPUTS ROOT_PRIVATE_OUTPUT required')
    print(json.dumps(finish(*args[1:], verify=args[0] == MODES[1]), sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Installation input publication incomplete; preserve original and partial directories; no installed targets changed', file=sys.stderr)
        sys.exit(1)
